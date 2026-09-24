"""Support by fire -> assault -> close combat (mnsim/assault.py)."""
import copy
import math
from pathlib import Path

from mnsim.model import Order, UnitState
from mnsim.scenario import load_scenario
from mnsim.terrain import TerrainModel

ROOT = Path(__file__).resolve().parents[1]


def _duel(ratio=3, seed=3, assault=True, hold=True):
    sim = load_scenario(str(ROOT / "scenarios" / "demo.json"), bml_files={})
    sim.rng.seed(seed)
    sim.combat_config["assault"] = {"enabled": assault}
    sim.terrain = TerrainModel({}, sim.combat_config.get("navigation", {})); sim.terrain.world = sim.world
    for u in list(sim.units.values()):
        if u.uid not in ("B-INF-2", "R-INF-2"):
            u.active = False
    b, r = sim.units["B-INF-2"], sim.units["R-INF-2"]
    r.elements = copy.deepcopy(b.elements)
    for e in b.elements.values():
        e.count *= ratio; e.initial_count *= ratio
    b.pos, r.pos = (1500.0, 2000.0), (2200.0, 2000.0)
    b.watch_heading_deg, r.watch_heading_deg = 0.0, 180.0
    b.order_queue.clear(); r.order_queue.clear()
    b.current_order = Order("a", "ATTACK", {"destination": [2200.0, 2000.0]})
    r.current_order = Order("d", "DEFEND", {"duration_s": 99999},
                            directives={"hold_at_all_costs": True} if hold else {})
    return sim, b, r


def test_superior_attacker_assaults_and_takes_the_objective():
    sim, b, r = _duel(ratio=3, assault=True)
    for _ in range(int(2000 / 0.25)):
        sim.step(0.25)
    assert any(e["kind"] == "ASSAULT_START" for e in sim.logs)
    assert math.dist(b.pos, (2200.0, 2000.0)) < 60.0


def test_without_assault_the_attack_stalls_short_of_the_position():
    sim, b, r = _duel(ratio=3, assault=False)
    for _ in range(int(800 / 0.25)):
        sim.step(0.25)
    assert not any(e["kind"] == "ASSAULT_START" for e in sim.logs)
    assert math.dist(b.pos, r.pos) > 400.0      # still sitting on the stand-off line


def test_assault_waits_for_support_by_fire_and_uses_contact_range():
    sim, b, r = _duel()
    tr_like = type("T", (), {})()
    b.metadata["_assault"] = {"target": r.uid, "phase": "ASSAULT"}
    assert sim.doctrine.desired_direct_engagement_range(b, r) == sim.assault.cfg()["contact_m"]
    b.metadata["_assault"]["phase"] = "SUPPORT"
    assert sim.doctrine.desired_direct_engagement_range(b, r) != sim.assault.cfg()["contact_m"]


def test_assault_is_called_off_when_the_attacker_is_shaken():
    sim, b, r = _duel()
    sim.combat_config["stress_model"] = {"enabled": True}
    b.target_id = r.uid
    from mnsim.model import Track
    b.local_tracks[r.uid] = Track(track_id="t", target_id=r.uid, estimated_pos=r.pos, position_error_m=5.0,
                                  classification="INFANTRY", confidence=0.9, last_seen_time=sim.time,
                                  source="LOCAL", state="IDENTIFIED")
    b.metadata["_assault"] = {"target": r.uid, "phase": "ASSAULT", "start_strength": b.current_strength}
    sim.stress.on_losses(b, 0.4)            # morale 1.0 -> 0.4: SHAKEN
    sim.assault.update(0.25)
    assert b.metadata["_assault"]["phase"] == "SUPPORT"
    assert any(e["kind"] == "ASSAULT_FAILED" for e in sim.logs)


def test_close_combat_breaks_the_weaker_side():
    sim, b, r = _duel(ratio=3)
    sim.combat_config["stress_model"] = {"enabled": True}
    r.pos = (b.pos[0] + 20.0, b.pos[1])
    r.state = UnitState.DEFENDING
    before = r.morale
    for _ in range(4):
        sim.assault._close_combat_round(b, r)
        sim.time += 1.0
        for ev in sim.events.pop_due(sim.time):
            sim._handle_event(ev.kind, ev.payload)
    assert r.morale < before - 0.3
    assert any(e["kind"] == "CLOSE_COMBAT" and e["power_ratio"] > 1.0 for e in sim.logs)


def test_assault_can_be_disabled_by_directive():
    sim, b, r = _duel()
    b.current_order.directives["assault"] = False
    assert not sim.assault._assault_allowed(b)
