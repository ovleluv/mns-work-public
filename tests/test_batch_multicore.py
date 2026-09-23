"""Independent process runs retain single-run results and input order."""

import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

from mnsim.batch import BatchRun, run_batch
from mnsim.scenario import load_scenario


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "scenarios" / "demo.json"


def test_parallel_batch_matches_serial_results_for_distinct_seeds():
    runs = [BatchRun(str(SCENARIO), steps=100, seed=seed, run_id=f"seed-{seed}")
            for seed in (7, 19, 41)]
    serial = run_batch(runs, workers=1)
    parallel = run_batch(runs, workers=2)
    assert parallel == serial
    assert [result.run_id for result in parallel] == ["seed-7", "seed-19", "seed-41"]
    assert all(result.simulated_time_s == 25.0 for result in parallel)
    assert all(set(result.sides) == {"BLUE", "RED"} for result in parallel)
    assert len({result.log_sha256 for result in parallel}) == len(parallel)


def test_batch_seed_override_does_not_change_default_scenario_seed():
    default = load_scenario(str(SCENARIO))
    override = load_scenario(str(SCENARIO), seed=999)
    again = load_scenario(str(SCENARIO), seed=999)
    first = override.rng.random()
    assert first == again.rng.random()
    assert first != default.rng.random()
    result = run_batch([BatchRun(str(SCENARIO), steps=0)], workers=1)[0]
    assert result.seed == default.seed


def test_batch_rejects_invalid_inputs_before_starting_workers():
    assert run_batch([], workers=4) == []
    with pytest.raises(ValueError, match="workers"):
        run_batch([], workers=0)
    with pytest.raises(ValueError, match="steps"):
        run_batch([BatchRun(str(SCENARIO), steps=-1)], workers=2)
    with pytest.raises(ValueError, match="dt_s"):
        run_batch([BatchRun(str(SCENARIO), steps=1, dt_s=math.nan)], workers=2)
    with pytest.raises(ValueError, match="finite clock range"):
        run_batch([BatchRun(str(SCENARIO), steps=10**400)], workers=2)
    with pytest.raises(FileNotFoundError, match="Scenario does not exist"):
        run_batch([BatchRun(str(ROOT / "missing.json"), steps=1)], workers=2)


def test_batch_respects_per_run_bml_selection():
    mission = ROOT / "MISSION" / "ATTACK" / "ATTACK_SCENARIO.json"
    loaded, ignored = run_batch([
        BatchRun(str(mission), steps=0),
        BatchRun(str(mission), steps=0, bml_files={}),
    ], workers=2)
    assert loaded.event_counts.get("BML_LOADED") == 2
    assert ignored.event_counts.get("BML_LOADED", 0) == 0


def test_batch_cli_emits_ordered_json_from_processes():
    result = subprocess.run(
        [sys.executable, "-m", "mnsim.batch", str(SCENARIO), "--runs", "2",
         "--seed-start", "9", "--steps", "4", "--workers", "2"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    data = json.loads(result.stdout)
    assert data["runs"] == 2 and data["workers"] == 2
    assert [run["seed"] for run in data["results"]] == [9, 10]
    assert all(run["simulated_time_s"] == 1.0 for run in data["results"])


def test_cli_distinguishes_paths_with_the_same_name():
    result = subprocess.run(
        [sys.executable, "-m", "mnsim.batch", str(SCENARIO), str(SCENARIO),
         "--steps", "0", "--workers", "2"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    ids = [run["run_id"] for run in json.loads(result.stdout)["results"]]
    assert len(ids) == len(set(ids)) == 2
