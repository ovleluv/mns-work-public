"""Behavioral regressions for perception, ownership, and BML."""

import json

import pytest

from mnsim.bml import ConditionEvaluator, apply_bml_document
from mnsim.model import Order, Track
from mnsim.scenario import load_scenario
from mnsim.simulation import Simulation
from mnsim.terrain import TerrainModel


def demo():
    return load_scenario("scenarios/demo.json", bml_files={})


def test_belief_half_life_is_independent_of_scan_count():
    sim = Simulation()
    tr = Track("B:R", "R", (0, 0), 10, belief_confidence=1.0,
               existence_confirmed=True, last_confirmed_time=0)
    for second in range(1, 136):
        sim.time = float(second)
        sim.belief.age(tr)
    assert tr.belief_confidence == pytest.approx(2 ** (-45 / 900))
    sim.time = 990.0
    sim.belief.age(tr)
    assert tr.belief_confidence == pytest.approx(0.5)


def test_delayed_report_replaces_stale_local_position_and_keeps_observation_age():
    sim = demo()
    receiver, target = sim.units["B-INF-2"], sim.units["R-INF-1"]
    sim.time = 100.0
    receiver.local_tracks[target.uid] = Track(
        "old", target.uid, (10, 10), 10, classification="INFANTRY",
        confidence=.7, last_seen_time=0, source="LOCAL", state="STALE",
        existence_confirmed=True)
    sim._receive_comm_message(receiver, {
        "message_type": "TRACK_REPORT", "sender_uid": "B-ART-1",
        "payload": {"target": target.uid, "estimated_pos": (99, 99),
                    "confidence": .6, "observation_time": 20,
                    "classification": "INFANTRY", "state": "CLASSIFIED"}})
    tr = receiver.local_tracks[target.uid]
    assert tr.estimated_pos == (99, 99)
    assert tr.last_seen_time == tr.last_confirmed_time == 20


def test_direct_fire_spends_a_round_but_cannot_hit_beyond_physical_range():
    sim = demo()
    shooter, target = sim.units["B-INF-2"], sim.units["R-INF-1"]
    element = shooter.elements["rifle_1"]
    weapon = next(w for w in element.weapons if w.name == "small arms")
    sim.terrain = TerrainModel({})
    target.pos = (shooter.pos[0] + 400, shooter.pos[1])
    shooter.watch_heading_deg = 0
    shooter.local_tracks[target.uid] = Track(
        "local", target.uid, (shooter.pos[0] + 300, shooter.pos[1]), 100,
        classification="INFANTRY", confidence=.9, last_seen_time=sim.time,
        source="LOCAL", state="IDENTIFIED")
    weapon.metadata.update(acquisition_delay_min_s=0, acquisition_delay_max_s=0)
    sim.rng.random = lambda: 0.0
    sim.combat._fire_weapon_once(shooter, target, element, weapon)
    sim.combat._fire_weapon_once(shooter, target, element, weapon)
    shots = [r for r in sim.logs if r["kind"] == "FIRE"]
    assert len(shots) == 1 and shots[0]["distance"] == 300.0
    assert shots[0]["hit"] is False
    assert not any(e.kind in ("ELEMENT_LOSS", "EQUIPMENT_EFFECT") for e in sim.events._q)


def test_unknown_tracks_do_not_reveal_actual_target_components():
    sim = demo()
    shooter = sim.units["B-INF-2"]
    infantry, armor = sim.units["R-INF-1"], sim.units["R-TK-1"]
    for element in shooter.elements.values():
        element.weapons = [w for w in element.weapons if w.name == "small arms"]
    for target in (infantry, armor):
        shooter.local_tracks[target.uid] = Track(
            shooter.uid + ":" + target.uid, target.uid,
            (shooter.pos[0] + 100, shooter.pos[1]), 10,
            classification="UNKNOWN", confidence=.9, last_seen_time=sim.time,
            source="LOCAL", state="DETECTED")
    assert sim._unit_can_affect(shooter, infantry)
    assert sim._unit_can_affect(shooter, armor)
    element = shooter.elements["rifle_1"]
    weapon = element.weapons[0]
    weapon.metadata.update(acquisition_delay_min_s=0, acquisition_delay_max_s=0)
    sim.combat_config["direct_fire_requires_watch_alignment"] = False
    sim.terrain = TerrainModel({})
    sim.rng.random = lambda: 0.0
    sim.combat._fire_weapon_once(shooter, armor, element, weapon)
    sim.combat._fire_weapon_once(shooter, armor, element, weapon)
    shots = [r for r in sim.logs if r["kind"] == "FIRE" and r["target"] == armor.uid]
    assert len(shots) == 1 and shots[0]["hit"] is False
    assert shots[0]["target_element"] is None


def test_unobserved_target_destruction_does_not_complete_entity_mission():
    sim = demo()
    actor, target = sim.units["B-INF-2"], sim.units["R-INF-1"]
    actor.current_order = Order("attack", "ATTACK_UNIT", {"target_unit": target.uid})
    target.active = False
    sim._step_unit(actor, .25)
    assert actor.current_order is not None
    assert not any(r["kind"] == "BML_TARGET_DESTROYED_CONFIRMED" for r in sim.logs)


def test_enemy_count_condition_uses_tracks_instead_of_ground_truth():
    sim = demo()
    actor, target = sim.units["B-INF-2"], sim.units["R-INF-1"]
    target.pos = (actor.pos[0] + 5, actor.pos[1])
    assert ConditionEvaluator.resolve(sim, actor, "self.enemy_count_near") == 0
    target.pos = (actor.pos[0] + 2000, actor.pos[1])
    actor.local_tracks[target.uid] = Track(
        "seen", target.uid, (actor.pos[0] + 50, actor.pos[1]), 10,
        confidence=.9, last_seen_time=sim.time, source="LOCAL", state="IDENTIFIED")
    assert ConditionEvaluator.resolve(sim, actor, "self.enemy_count_near") == 1


def test_completion_branch_sees_reached_objective_before_cleanup():
    sim = demo()
    actor = sim.units["B-INF-2"]
    actor.order_queue.clear()
    actor.current_order = Order(
        "move", "MOVE", {"destination": actor.pos},
        conditions=[{"lhs": "self.at_objective", "op": "==", "rhs": True}],
        on_true={"kind": "WAIT"}, on_false={"kind": "HOLD"})
    sim.time = 1.0
    sim._step_unit(actor, .25)
    assert [order.kind for order in actor.order_queue] == ["WAIT"]


def test_nested_bml_error_rejects_entire_document_before_queue_changes():
    sim = demo()
    actor = sim.units["B-INF-2"]
    original = list(actor.order_queue)
    with pytest.raises(ValueError, match="unsupported BML mission task"):
        apply_bml_document(sim, {"side": "BLUE", "missions": [
            {"unit": actor.uid, "task": "HOLD",
             "conditions": [{"lhs": "sim.time", "rhs": 0}],
             "on_true": {"task": "UNSUPPORTED_TASK"}}]}, expected_side="BLUE")
    assert actor.order_queue == original
    with pytest.raises(ValueError, match="unsupported BML directives"):
        apply_bml_document(sim, {"side": "BLUE", "missions": [
            {"unit": actor.uid, "task": "HOLD", "directives": {"hold_fire": True}}]},
            expected_side="BLUE")
    assert actor.order_queue == original


def test_out_of_world_orders_are_rejected_and_runtime_movement_stays_bounded():
    sim = demo()
    actor = sim.units["B-INF-2"]
    with pytest.raises(ValueError, match="outside the scenario world"):
        apply_bml_document(sim, {"side": "BLUE", "missions": [
            {"unit": actor.uid, "task": "MOVE_TO", "destination": [-10, 100]}]},
            expected_side="BLUE")
    actor.pos = (1, 100)
    sim.terrain = TerrainModel({})
    sim.terrain.world = sim.world
    sim._move_toward(actor, (-10, 100), 10)
    assert actor.pos == (1, 100)


def test_same_id_on_separate_orders_reports_each_deadline():
    sim = demo()
    actor = sim.units["B-INF-2"]
    actor.order_queue.clear()
    actor.current_order = Order("reused", "WAIT", {"duration_s": 0}, deadline_s=0)
    sim.time = 1.0
    sim._step_unit(actor, .25)
    actor.current_order = Order("reused", "HOLD", {"duration_s": -1}, deadline_s=0)
    sim.time = 2.0
    sim._step_unit(actor, .25)
    assert sum(r["kind"] == "ORDER_DEADLINE_MISSED" for r in sim.logs) == 2


def test_in_flight_damage_follows_equipment_through_aggregation_and_split():
    sim = demo()
    tank = sim.units["B-TK-1"]
    sim.events.push(.15, "EQUIPMENT_EFFECT", target=tank.uid, element="tanks",
                    item_index=0, effect="DESTROYED", source="R-ART-1")
    parent = sim.aggregate_units("P", "company", [tank.uid])
    sim.time = .15
    for event in sim.events.pop_due(sim.time):
        sim._handle_event(event.kind, event.payload)
    assert parent.elements[f"{tank.uid}:tanks"].item_states[0] == "DESTROYED"
    sim.events.push(.30, "EQUIPMENT_EFFECT", target=parent.uid,
                    element=f"{tank.uid}:tanks", item_index=1,
                    effect="DESTROYED", source="R-ART-1")
    sim.deaggregate_unit(parent.uid)
    sim.time = .30
    for event in sim.events.pop_due(sim.time):
        sim._handle_event(event.kind, event.payload)
    assert tank.elements["tanks"].item_states[:2] == ["DESTROYED", "DESTROYED"]


def test_in_flight_personnel_loss_follows_aggregated_element():
    sim = demo()
    infantry = sim.units["B-INF-2"]
    before = infantry.elements["rifle_1"].count
    sim.events.push(.15, "ELEMENT_LOSS", target=infantry.uid,
                    element="rifle_1", count=1, source="R-INF-1")
    parent = sim.aggregate_units("P-INF", "company", [infantry.uid])
    sim.time = .15
    for event in sim.events.pop_due(sim.time):
        sim._handle_event(event.kind, event.payload)
    assert parent.elements[f"{infantry.uid}:rifle_1"].count == before - 1


def test_load_time_aggregate_can_receive_bml_parent_order(tmp_path):
    scenario = {
        "unit_types": {"INF": {"branch": "INFANTRY", "max_speed_mps": 1,
            "detection_range_m": 100, "elements": [
                {"id": "rifle", "category": "PERSONNEL", "role": "RIFLE",
                 "count": 5, "weapons": []}]}},
        "units": [{"id": "B1", "side": "BLUE", "type": "INF", "pos": [100, 100]}],
        "aggregations": [{"id": "P", "children": ["B1"]}]}
    scenario_path = tmp_path / "scenario.json"
    bml_path = tmp_path / "blue.json"
    scenario_path.write_text(json.dumps(scenario))
    bml_path.write_text(json.dumps({"side": "BLUE", "missions": [
        {"unit": "P", "task": "MOVE_TO", "destination": [200, 100]}]}))
    sim = load_scenario(str(scenario_path), bml_files={"BLUE": str(bml_path)})
    assert [order.kind for order in sim.units["P"].order_queue] == ["MOVE"]
    assert not sim.units["B1"].active
