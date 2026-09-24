"""Suppression, morale and prepared positions (mnsim/stress.py)."""
from pathlib import Path

from mnsim.model import Order, UnitState
from mnsim.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[1]


def _sim():
    sim = load_scenario(str(ROOT / "scenarios" / "demo.json"), bml_files={})
    sim.combat_config["stress_model"] = {"enabled": True}   # off by default; opt in here
    return sim


def test_stress_model_is_disabled_by_default():
    sim = load_scenario(str(ROOT / "scenarios" / "demo.json"), bml_files={})
    assert not sim.stress.enabled


def _rifle(u):
    return next(w for _, w in u.operational_weapons() if "PERSONNEL" in [t.upper() for t in w.target_tags])


def test_incoming_fire_suppresses_and_decays():
    sim = _sim(); tgt = sim.units["R-INF-1"]; shooter = sim.units["B-INF-2"]
    w = _rifle(shooter)
    for _ in range(40):
        sim.stress.on_direct_fire(tgt, shooter, w, 200.0, hit=False)
    s = tgt.suppression
    assert s > 0.3
    sim.stress.update(30.0)                        # two half-lives
    assert tgt.suppression < s * 0.3


def test_suppression_scales_down_for_large_formations():
    sim = _sim(); small = sim.units["R-INF-1"]; shooter = sim.units["B-INF-2"]; w = _rifle(shooter)
    big = sim.units["R-INF-2"]
    for e in big.elements.values():
        if e.category.upper() == "PERSONNEL":
            e.count *= 10; e.initial_count *= 10
    sim.stress.on_direct_fire(small, shooter, w, 200.0, hit=False)
    sim.stress.on_direct_fire(big, shooter, w, 200.0, hit=False)
    assert big.suppression < small.suppression


def test_suppression_degrades_accuracy_rate_movement_and_detection():
    sim = _sim(); u = sim.units["B-INF-2"]
    u.suppression = 0.8
    assert sim.stress.accuracy_factor(u) < 0.6
    assert sim.stress.fire_interval_factor(u) > 1.9
    assert sim.stress.movement_factor(u) < 0.45
    assert sim.stress.detection_factor(u) < 0.7


def test_heavy_losses_break_a_formation_and_it_falls_back():
    sim = _sim(); u = sim.units["B-INF-2"]
    u.current_order = Order(order_id="A", kind="ATTACK", params={"destination": [3000.0, 2000.0]})
    sim.stress.on_losses(u, 0.5)
    assert sim.stress.morale_state(u) == "BROKEN"
    u.metadata["threat_cue_heading_deg"] = 0.0; u.metadata["threat_cue_until_t"] = sim.time + 30
    start = u.pos
    for _ in range(40):
        sim.step(0.25)
    assert u.metadata.get("tactical_reason", "").startswith("MORALE BROKEN")
    assert u.pos[0] < start[0]                     # moved away from the eastern threat
    assert any(e["kind"] == "MORALE_BROKEN" for e in sim.logs)


def test_hold_at_all_costs_lowers_the_break_point():
    sim = _sim(); u = sim.units["B-INF-2"]
    u.current_order = Order(order_id="H", kind="HOLD", directives={"hold_at_all_costs": True})
    sim.stress.on_losses(u, 0.5)
    assert sim.stress.morale_state(u) != "BROKEN"


def test_pinned_attacker_goes_to_ground():
    sim = _sim(); u = sim.units["B-INF-2"]
    u.current_order = Order(order_id="A", kind="ATTACK", params={"destination": [3000.0, 2000.0]})
    u.suppression = 0.95
    assert sim.doctrine._stress_behavior(u, [], 0.25)
    assert u.state == UnitState.DEFENDING and "PINNED" in u.metadata["tactical_reason"]


def test_prepared_position_protection_builds_up_over_time():
    sim = _sim(); u = sim.units["B-INF-2"]
    u.state = UnitState.DEFENDING
    u.metadata["_defending_since"] = sim.time
    hasty = sim.dig_in_fraction(u)
    sim.time += 2000.0
    assert hasty < sim.dig_in_fraction(u) == 1.0


def test_stress_model_can_be_disabled():
    sim = _sim(); u = sim.units["B-INF-2"]
    sim.combat_config["stress_model"] = {"enabled": False}
    u.suppression = 0.9
    assert sim.stress.accuracy_factor(u) == 1.0 and sim.stress.morale_state(u) == "STEADY"


def test_outranged_defender_withdraws_after_sustained_unanswerable_fire():
    sim = _sim(); u = sim.units["R-INF-2"]
    u.current_order = Order(order_id="D", kind="DEFEND", params={"duration_s": 9999})
    u.local_tracks.clear()                                 # the shooter is not even seen
    start = u.pos
    for _ in range(int(40 / 0.25)):
        u.metadata["threat_cue_type"] = "DIRECT_FIRE"
        u.metadata["threat_cue_source_uid"] = "B-INF-2"
        u.metadata["threat_cue_heading_deg"] = 180.0       # fire from the west
        u.metadata["threat_cue_until_t"] = sim.time + 5.0
        sim.doctrine._outranged_reaction(u, 0.25)
        sim.time += 0.25
    assert any(e["kind"] == "OUTRANGED_WITHDRAW" for e in sim.logs)
    assert u.pos[0] > start[0]                             # moved east, away from the fire


def test_hold_at_all_costs_does_not_withdraw_when_outranged():
    sim = _sim(); u = sim.units["R-INF-2"]
    u.current_order = Order(order_id="D", kind="HOLD", directives={"hold_at_all_costs": True})
    u.local_tracks.clear()
    for _ in range(200):
        u.metadata.update(threat_cue_type="DIRECT_FIRE", threat_cue_source_uid="B-INF-2",
                          threat_cue_heading_deg=180.0, threat_cue_until_t=sim.time + 5.0)
        assert not sim.doctrine._outranged_reaction(u, 0.25)
        sim.time += 0.25
