"""Detection is a per-second hazard; signature depends on size and recent firing."""
import statistics
from pathlib import Path

from mnsim.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[1]


def _mean_time_to_detect(interval, seeds=30):
    ts = []
    for seed in range(seeds):
        sim = load_scenario(str(ROOT / "scenarios" / "demo.json"), bml_files={})
        sim.rng.seed(seed)
        sim.combat_config["sensor_update_s"] = interval
        sim.combat_config["watch_sweep"] = {"enabled": False}   # isolate the hazard from scanning
        for u in sim.units.values():
            u.order_queue = []; u.current_order = None
        b, r = sim.units["B-TK-1"], sim.units["R-TK-1"]
        b.pos = (r.pos[0] - 900, r.pos[1]); b.watch_heading_deg = 0.0
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


def test_classification_can_be_wrong_until_identified_and_sticks():
    from mnsim.model import Track
    sim = load_scenario(str(ROOT / "scenarios" / "demo.json"), bml_files={})
    tank = sim.units["R-TK-1"]
    sim.combat_config["classification_error"] = {"enabled": True, "max_error_probability": 1.0}
    assert sim._perceived_class(None, tank, 0.52) == "MECH_INFANTRY"
    prev = Track(track_id="t", target_id=tank.uid, estimated_pos=tank.pos, position_error_m=10.0,
                 classification="MECH_INFANTRY", state="CLASSIFIED")
    sim.combat_config["classification_error"] = {"enabled": True, "max_error_probability": 0.0}
    assert sim._perceived_class(prev, tank, 0.7) == "MECH_INFANTRY"      # sticky judgement
    assert sim._perceived_class(None, tank, 0.9) == "ARMOR"              # confident -> correct
