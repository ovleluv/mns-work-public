"""Run independent scenarios across CPU cores without splitting a live simulation tick.

Each worker constructs its own Simulation, RNG, terrain, and event queue. Only compact results
cross process boundaries; no mutable engine objects or full event streams are pickled.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path

from .model import Side, UnitState
from .scenario import load_scenario


@dataclass(frozen=True)
class BatchRun:
    scenario_path: str
    steps: int
    dt_s: float = 0.25
    seed: int | None = None
    bml_files: dict[str, str] | None = None
    run_id: str | None = None


@dataclass(frozen=True)
class BatchResult:
    run_id: str
    scenario_path: str
    seed: int
    simulated_time_s: float
    sides: dict[str, dict[str, int]]
    event_counts: dict[str, int]
    log_sha256: str


def _prepare_run(run: BatchRun) -> BatchRun:
    if not isinstance(run, BatchRun):
        raise TypeError("runs must contain BatchRun values")
    if isinstance(run.steps, bool) or not isinstance(run.steps, int) or run.steps < 0:
        raise ValueError("steps must be a non-negative integer")
    dt = float(run.dt_s)
    if not math.isfinite(dt) or dt <= 0.0:
        raise ValueError("dt_s must be finite and positive")
    try:
        total_time = float(run.steps) * dt
    except OverflowError as exc:
        raise ValueError("simulated duration exceeds the finite clock range") from exc
    if not math.isfinite(total_time):
        raise ValueError("simulated duration exceeds the finite clock range")
    if run.seed is not None and (isinstance(run.seed, bool) or not isinstance(run.seed, int)):
        raise ValueError("seed must be an integer or None")
    path = Path(run.scenario_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Scenario does not exist: {path}")
    bml_files = (None if run.bml_files is None else
                 {str(side).upper(): str(Path(ref).expanduser().resolve())
                  for side, ref in run.bml_files.items()})
    return replace(run, scenario_path=str(path), dt_s=dt, bml_files=bml_files)


def _worker_count(requested: int | None, jobs: int) -> int:
    if requested is None:
        requested = min(4, os.cpu_count() or 1)
    if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
        raise ValueError("workers must be a positive integer")
    return min(requested, max(1, jobs))


def _run_one(item: tuple[int, BatchRun]) -> BatchResult:
    index, run = item
    sim = load_scenario(run.scenario_path, bml_files=run.bml_files, seed=run.seed)
    for _ in range(run.steps):
        sim.tick(run.dt_s)
    sides = {}
    for side in Side:
        units = [unit for unit in sim.units.values() if unit.side == side]
        # Aggregated parents own the inventory while their inactive source snapshots do not.
        # An inactive embarked passenger still owns its personnel and must be counted.
        owners = [unit for unit in units
                  if unit.state != UnitState.AGGREGATED or unit.metadata.get("embarked_in")]
        sides[side.value] = {
            "active_units": sum(unit.alive for unit in units),
            "personnel": sum(unit.personnel for unit in owners),
            "equipment": sum(unit.equipment for unit in owners),
        }
    digest = hashlib.sha256()
    for record in sim.logs:
        digest.update(json.dumps(record, sort_keys=True, ensure_ascii=False,
                                 separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
    return BatchResult(
        run_id=run.run_id or str(index + 1), scenario_path=run.scenario_path,
        seed=int(sim.seed), simulated_time_s=sim.time, sides=sides,
        event_counts=dict(sorted(Counter(record["kind"] for record in sim.logs).items())),
        log_sha256=digest.hexdigest(),
    )


def run_batch(runs: list[BatchRun], workers: int | None = None) -> list[BatchResult]:
    """Return results in input order; workers=1 uses the same code path without process startup.

    The useful parallel unit is a complete independent run. Splitting one tick would require
    transferring changing terrain, tracks, RNG and queued events every 0.25 seconds, and would
    disturb deterministic event order. CPU-bound Python threads do not bypass the GIL.
    """
    prepared = [_prepare_run(run) for run in runs]
    count = _worker_count(workers, len(prepared))
    if count == 1:
        return [_run_one(item) for item in enumerate(prepared)]
    with ProcessPoolExecutor(max_workers=count) as pool:
        return list(pool.map(_run_one, enumerate(prepared)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run independent simulator cases across CPU cores")
    parser.add_argument("scenarios", nargs="+", help="Scenario JSON paths")
    parser.add_argument("--runs", type=int, default=1, help="Seeds to run per scenario (default: 1)")
    parser.add_argument("--seed-start", type=int, default=7, help="First seed for each scenario")
    parser.add_argument("--steps", type=int, default=600, help="Simulation ticks per run")
    parser.add_argument("--dt", type=float, default=0.25, help="Simulated seconds per tick")
    parser.add_argument("--workers", type=int, default=None, help="Worker processes (default: up to 4)")
    parser.add_argument("--ignore-embedded-bml", action="store_true",
                        help="Run without the scenario's embedded BML plans")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be positive")
    runs = [BatchRun(scenario, steps=args.steps, dt_s=args.dt, seed=args.seed_start + index,
                     bml_files={} if args.ignore_embedded_bml else None,
                     run_id=f"{Path(scenario).stem}-{scenario_index + 1}-seed{args.seed_start + index}")
            for scenario_index, scenario in enumerate(args.scenarios) for index in range(args.runs)]
    try:
        results = run_batch(runs, args.workers)
    except (TypeError, ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
    print(json.dumps({"runs": len(results), "workers": _worker_count(args.workers, len(results)),
                      "results": [asdict(result) for result in results]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
