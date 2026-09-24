"""Regression tests for determinism, timestep independence, FoW and stable item identity."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from mnsim.model import Track, UnitState
from mnsim.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[1]

_RUN = r"""
import hashlib, json, sys
sys.path.insert(0, sys.argv[1])
from mnsim.scenario import load_scenario
sim = load_scenario(sys.argv[1] + "/scenarios/demo.json")
for _ in range(480):
    sim.tick(0.25)
print(hashlib.sha256(json.dumps(sim.logs, sort_keys=True, default=str).encode()).hexdigest())
"""


def test_same_seed_is_reproducible_across_hash_seeds():
    """Set iteration order must never reach the RNG draw sequence."""
    digests = set()
    for hash_seed in ("1", "2"):
        env = dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONDONTWRITEBYTECODE="1")
        out = subprocess.run([sys.executable, "-c", _RUN, str(ROOT)], env=env,
                             capture_output=True, text=True, check=True, timeout=300)
        digests.add(out.stdout.strip())
    assert len(digests) == 1


def test_sensor_scan_count_does_not_depend_on_step_size():
    counts = []
    for dt in (0.25, 0.2286, 0.1):
        sim = load_scenario(str(ROOT / "scenarios" / "demo.json"))
        calls = []
        original = sim._sensor_step
        sim._sensor_step = lambda: (calls.append(sim.time), original())[1]
        steps = int(round(120.0 / dt))
        for _ in range(steps):
            sim.step(dt)
        counts.append(len(calls))
    assert max(counts) - min(counts) <= 1, counts


def test_events_are_handled_at_their_own_timestamp():
    sim = load_scenario(str(ROOT / "scenarios" / "demo.json"))
    seen = []
    original = sim._handle_event
    sim._handle_event = lambda kind, p: (seen.append((kind, sim.time)), original(kind, p))[1]
    sim.events.push(0.10, "TRACK_SHARE", source=None)
    sim.step(0.25)
    assert ("TRACK_SHARE", 0.10) in seen
    assert sim.time == 0.25


def _local_track(target, now, cls, tags=None):
    return Track(track_id=f"x:{target.uid}", target_id=target.uid, estimated_pos=target.pos,
                 position_error_m=5.0, classification=cls, confidence=0.9, last_seen_time=now,
                 observations=4, source="LOCAL", observation_zone="FORWARD", state="IDENTIFIED",
                 perceived_tags=tags)


def test_weapon_choice_uses_perceived_composition_snapshot():
    sim = load_scenario(str(ROOT / "scenarios" / "demo.json"))
    shooter = sim.units["B-INF-2"]
    target = sim.units["R-INF-1"]
    rifle = next(w for _, w in shooter.operational_weapons() if "PERSONNEL" in [t.upper() for t in w.target_tags])
    snapshot = sim.combat.observed_target_tags(target)
    tr = _local_track(target, sim.time, "INFANTRY", snapshot)
    assert sim.combat.weapon_can_affect_perceived(rifle, tr)
    # Kill every exposed person in ground truth: the shooter has not observed that yet.
    for e in target.elements.values():
        if e.category.upper() == "PERSONNEL":
            e.count = 0
    assert not sim.combat.weapon_can_affect(rifle, target)          # physics
    assert sim.combat.weapon_can_affect_perceived(rifle, tr)         # belief (unchanged)


def test_infantry_break_contact_uses_classification_not_true_branch():
    sim = load_scenario(str(ROOT / "scenarios" / "demo.json"))
    inf = sim.units["B-INF-2"]
    tank = next(u for u in sim.units.values() if u.side.value == "RED" and u.branch == "ARMOR")
    tr = _local_track(tank, sim.time, "INFANTRY")   # misclassified contact
    assert not sim.combat.track_indicates_armor(tr)
    tr.classification = "ARMOR"
    assert sim.combat.track_indicates_armor(tr)


def test_attack_unit_completes_only_after_battle_damage_information():
    sim = load_scenario(str(ROOT / "scenarios" / "demo.json"))
    b = sim.units["B-TK-1"]
    target = sim.units["R-INF-1"]
    from mnsim.model import Order
    b.order_queue.clear(); b.current_order = Order(order_id="kill", kind="ATTACK_UNIT",
                                                   params={"target_unit": target.uid})
    b.local_tracks.clear()
    target.state = UnitState.DESTROYED          # ground truth only
    sim._step_entity_attack_order(b, b.current_order, 0.25)
    assert b.current_order is not None, "must not learn the kill from ground truth"
    sim._apply_bda(b, target.uid, {"estimated_pos": target.pos}, source="TEST")
    sim._step_entity_attack_order(b, b.current_order, 0.25)
    assert b.current_order is None


def test_witness_of_a_kill_gets_bda_and_unobserved_kill_stays_unknown():
    sim = load_scenario(str(ROOT / "scenarios" / "demo.json"))
    target = sim.units["R-INF-1"]
    watcher = sim.units["B-INF-2"]
    blind = sim.units["B-TK-1"]
    watcher.local_tracks[target.uid] = _local_track(target, sim.time, "INFANTRY")
    blind.local_tracks.pop(target.uid, None)
    sim._on_unit_destroyed(target, source_uid=None)
    assert watcher.local_tracks[target.uid].state == "DESTROYED"
    assert blind.local_tracks.get(target.uid) is None or blind.local_tracks[target.uid].state != "DESTROYED"
    assert any(e["kind"] == "BDA_REPORT" for e in sim.logs)


def test_delayed_artillery_effect_follows_the_physical_item_after_detachment():
    sim = load_scenario(str(ROOT / "scenarios" / "demo.json"))
    tank = next(u for u in sim.units.values() if u.branch == "ARMOR" and u.side.value == "RED")
    el = next(e for e in tank.elements.values() if e.category.upper() == "EQUIPMENT" and "ARMOR" in e.tags)
    el.ensure_item_states()
    assert len(el.item_states) >= 3
    third_id = el.item_id_at(2)
    # Artillery addresses items 0 and 2 in the same impact; item 0 is detached first.
    sim.damage.apply_equipment_effect(tank, el, "MOBILITY_KILL", item_index=0)
    assert el.index_of_item(third_id) == 1        # list shifted
    sim._handle_event("EQUIPMENT_EFFECT", {"target": tank.uid, "element": el.eid, "item_index": 2,
                                           "item_id": third_id, "effect": "DESTROYED",
                                           "source": "", "weapon": "TEST", "reason": "ARTILLERY_DIRECT"})
    assert el.item_states[el.index_of_item(third_id)] == "DESTROYED"
    others = [s for i, s in enumerate(el.item_states) if el.item_ids[i] != third_id]
    assert all(s == "OPERATIONAL" for s in others)


def test_attack_unit_ends_when_target_is_not_found():
    from mnsim.model import Order
    sim = load_scenario(str(ROOT / "scenarios" / "demo.json"))
    b = sim.units["B-TK-1"]
    b.order_queue.clear(); b.local_tracks.clear()
    b.current_order = Order(order_id="kill", kind="ATTACK_UNIT",
                            params={"target_unit": "R-INF-1", "search_timeout_s": 5.0})
    sim.units["R-INF-1"].state = UnitState.DESTROYED   # killed out of view
    for _ in range(30):
        b.local_tracks.clear()
        sim._step_entity_attack_order(b, b.current_order, 0.25) if b.current_order else None
        sim.time += 0.25
    assert b.current_order is None
    assert any(e["kind"] == "BML_TARGET_NOT_FOUND" for e in sim.logs)


def test_shooter_without_a_fresh_local_track_gets_no_bda():
    sim = load_scenario(str(ROOT / "scenarios" / "demo.json"))
    arty = sim.units["B-ART-1"]
    target = sim.units["R-ART-1"]
    tr = _local_track(target, sim.time - 100.0, "ARTILLERY")
    tr.source = "COUNTER_BATTERY"
    arty.local_tracks[target.uid] = tr
    sim._on_unit_destroyed(target, source_uid=arty.uid)
    assert arty.local_tracks[target.uid].state != "DESTROYED"


def test_deaggregated_children_get_distinct_offset_orders():
    from mnsim.model import Order
    sim = load_scenario(str(ROOT / "scenarios" / "demo.json"))
    kids = ["B-INF-2", "B-INF-3"]
    before = {k: sim.units[k].pos for k in kids}
    parent = sim.aggregate_units("B-COY", "coy", kids)
    parent.current_order = Order(order_id="adv", kind="MOVE", params={"destination": [2000.0, 2000.0]})
    sim.deaggregate_unit("B-COY")
    orders = [sim.units[k].current_order for k in kids]
    assert len({o.order_id for o in orders}) == 2
    dests = [tuple(o.params["destination"]) for o in orders]
    assert dests[0] != dests[1]
    for k in kids:
        assert sim.units[k].pos == before[k]


def test_scheduled_infrastructure_strike_waits_for_start_time():
    from mnsim.model import Order
    sim = load_scenario(str(ROOT / "scenarios" / "demo.json"), bml_files={})
    arty = sim.units["B-ART-2"]
    arty.order_queue.clear()
    arty.current_order = Order(order_id="S", kind="STRIKE_INFRASTRUCTURE",
                               params={"targets": ["BR1"]}, start_at_s=600.0)
    for _ in range(200):
        sim.step(0.25)
    assert not any(e["kind"] == "FIRE_MISSION_REQUEST" and e.get("mode") == "INFRASTRUCTURE_STRIKE"
                   for e in sim.logs)


def test_damage_does_not_fire_branch_of_a_gated_order():
    from mnsim.model import Order
    sim = load_scenario(str(ROOT / "scenarios" / "demo.json"), bml_files={})
    u = sim.units["B-INF-2"]
    u.order_queue.clear()
    u.current_order = Order(order_id="P2", kind="HOLD", params={}, start_at_s=600.0,
                            conditions=[{"lhs": "self.loss_ratio", "op": ">=", "rhs": 0.0}],
                            on_true={"task": "WITHDRAW", "destination": [100.0, 100.0]})
    sim._check_reactive_branches(u)
    assert u.current_order.order_id == "P2"


def test_shared_report_never_rolls_back_a_fresher_close_track():
    sim = load_scenario(str(ROOT / "scenarios" / "demo.json"), bml_files={})
    recv = sim.units["B-INF-2"]; tgt = sim.units["R-INF-1"]
    sim.time = 100.0
    close = _local_track(tgt, 100.0, "INFANTRY"); close.source = "PROXIMITY"; close.confidence = 0.88
    recv.local_tracks[tgt.uid] = close
    sim._receive_comm_message(recv, {"message_type": "TRACK_REPORT", "sender_uid": "B-TK-1", "payload": {
        "target": tgt.uid, "estimated_pos": [0.0, 0.0], "confidence": 0.99, "observation_time": 70.0,
        "classification": "INFANTRY", "state": "IDENTIFIED"}})
    assert recv.local_tracks[tgt.uid].last_seen_time == 100.0


def test_unit_on_collapsing_bridge_is_moved_to_a_bank():
    sim = load_scenario(str(ROOT / "scenarios" / "demo.json"), bml_files={})
    br = sim.terrain.bridge_by_id("BR1")
    tank = sim.units["B-TK-1"]
    tank.order_queue.clear(); tank.current_order = None
    tank.pos = tuple(sim.terrain.bridge_center(br))
    sim.step(0.25)
    br["integrity"] = 0.0; br["destroyed"] = True
    sim.step(0.25)
    assert sim.terrain.passable(tank, tank.pos)
    assert any(e["kind"] == "BRIDGE_COLLAPSE_UNIT_DISPLACED" for e in sim.logs)
