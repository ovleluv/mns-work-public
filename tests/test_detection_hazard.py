"""Detection is a per-second hazard; signature depends on size and recent firing."""
import statistics
from pathlib import Path

from mnsim.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[1]


def _mean_time_to_detect(interval, seeds=12):
    ts = []
    for seed in range(seeds):
        sim = load_scenario(str(ROOT / "scenarios" / "demo.json"), bml_files={})
        sim.rng.seed(seed)
        sim.combat_config["sensor_update_s"] = interval
        for u in sim.units.values():
            u.order_queue = []; u.current_order = None
        b, r = sim.units["B-TK-1"], sim.units["R-TK-1"]
        b.pos = (r.pos[0] - 900, r.pos[1])
        hit = 300.0
        while sim.time < 300.0:
            sim.step(0.25)
            tr = b.local_tracks.get("R-TK-1")
            if tr and tr.source == "LOCAL":
                hit = sim.time; break
        ts.append(hit)
    return statistics.mean(ts)


def test_time_to_detect_does_not_depend_on_scan_interval():
    fast, slow = _mean_time_to_detect(0.5), _mean_time_to_detect(2.0)
    assert 0.6 < fast / slow < 1.6, (fast, slow)


def test_firing_and_size_raise_signature():
    sim = load_scenario(str(ROOT / "scenarios" / "demo.json"), bml_files={})
    u = sim.units["R-INF-1"]
    quiet = sim._target_signature(u)
    u.weapon_last_fire["x"] = sim.time
    assert sim._target_signature(u) > quiet * 1.4
    u.weapon_last_fire.clear()
    for e in u.elements.values():
        if e.category.upper() == "PERSONNEL":
            e.count = 1
    assert sim._target_signature(u) < quiet
