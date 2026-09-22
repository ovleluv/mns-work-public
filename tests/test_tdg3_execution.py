import hashlib
import json
import math
import os
import platform
import time
from pathlib import Path

import pytest

from tdg3_checks import audit_events


ROOT = Path(__file__).resolve().parents[1]


def isolate(sim, uid):
    unit = sim.units[uid]
    sim.units = {uid: unit}
    sim.paused, sim.speed = False, 1.0
    return unit


def test_b_security_standing_plan_waits_then_moves_without_radio(tdg3_sim):
    sim = tdg3_sim
    unit = isolate(sim, "B-SEC-B")
    initial = unit.pos
    for _ in range(720):
        sim.tick(.25)
    assert unit.pos == initial
    assert unit.current_order.kind == "HOLD"
    for _ in range(10):
        sim.tick(.25)
    assert unit.current_order.order_id == "B-SEC-B-STANDING-RALLY-EAST"
    assert math.dist(unit.pos, initial) > 0
    assert not any(r["kind"] in ("COMM_TX", "TRACK_SHARED") for r in sim.logs)


def test_failed_radio_blocks_both_directions_but_other_blue_reports_arrive(tdg3_sim):
    sim = tdg3_sim
    keep = ("B-SEC-B", "B-HQ", "B-ALPHA", "R-T55-1")
    sim.units = {uid: sim.units[uid] for uid in keep}
    for unit in sim.units.values():
        unit.current_order = None
        unit.order_queue.clear()
        unit.local_tracks.clear()
    sim._next_sensor_update = float("inf")  # Isolate radio delivery from local sensing.
    sim.combat_config["communications"]["default_link"].update(
        reliability=1.0, min_delay_s=.1, max_delay_s=.1)
    payload = {"target": "R-T55-1", "side": "BLUE", "estimated_pos": [1200, 400],
               "position_error_m": 20, "classification": "ARMOR", "confidence": .8,
               "state": "CLASSIFIED", "track_source": "SHARED", "observation_time": 0}
    assert not sim.communications.send("B-SEC-B", "B-HQ", "TRACK_REPORT", payload)
    assert not sim.communications.send("B-HQ", "B-SEC-B", "TRACK_REPORT", payload)
    assert sim.communications.send("B-HQ", "B-ALPHA", "TRACK_REPORT", payload)
    sim.tick(.2)
    assert "R-T55-1" not in sim.units["B-SEC-B"].local_tracks
    assert "R-T55-1" not in sim.units["B-HQ"].local_tracks
    assert sim.units["B-ALPHA"].local_tracks["R-T55-1"].source == "SHARED"


@pytest.mark.parametrize("uid,crew_id,weapon_id", [
    ("B-AT-1", "at_crew", "ATGM_GENERIC"), ("B-AT-2", "at_crew", "ATGM_GENERIC"),
    ("B-MG-1", "gun_crew", "GPMG_SINGLE_GENERIC"), ("B-MG-2", "gun_crew", "GPMG_SINGLE_GENERIC")])
def test_lone_weapon_crew_survivor_rallies_and_never_resumes_forward_orders(tdg3_sim, uid, crew_id, weapon_id):
    sim = tdg3_sim
    unit = isolate(sim, uid)
    unit.current_order = next(o for o in unit.order_queue if o.order_id == uid + "-BLOCK")
    unit.order_queue = [o for o in unit.order_queue if o.order_id == uid + "-RALLY-HOLD"]
    unit.elements[crew_id].count = 1
    unit.pos = (1730, 1252)  # Short real-terrain approach to ORV; no hostile contact.
    assert not any(w.metadata.get("weapon_id") == weapon_id for _, w in unit.operational_weapons())
    assert unit.operational_weapons(), "Survivor needs a usable personal weapon for withdrawal doctrine"
    sim.tick(.25)
    assert unit.current_order.kind == "RETREAT"
    initial = unit.pos
    for _ in range(800):
        sim.tick(.25)
        if unit.current_order and unit.current_order.kind == "HOLD":
            break
    assert math.dist(unit.pos, initial) > 0
    assert math.dist(unit.pos, tuple(sim.objectives["ORV"])) <= 10
    assert unit.current_order.order_id == uid + "-RALLY-HOLD"
    final = unit.pos
    for _ in range(20):
        sim.tick(.25)
    assert unit.pos == final and not unit.order_queue


@pytest.mark.parametrize("uid", ["R-T55-1", "R-T55-2", "R-T55-3", "R-T55-4"])
def test_red_departure_gate_prevents_early_movement(tdg3_sim, uid):
    sim = tdg3_sim
    tank = isolate(sim, uid)
    order = next(o for o in tank.order_queue if o.order_id.endswith("-CROSSING-APPROACH"))
    tank.current_order, tank.order_queue = order, []
    tank.pos = (1316, 760)
    before = tank.pos
    sim.time = order.start_at_s - .5
    sim.tick(.25)
    assert tank.pos == before
    sim.tick(.25)
    assert 0 < math.dist(before, tank.pos) < 5


def test_navigation_team_deadline_switches_to_flank_before_next_defence(tdg3_sim):
    sim = tdg3_sim
    unit = isolate(sim, "B-NAV")
    sim.tick(.25)
    before = unit.pos
    sim.time = 899.75
    sim.tick(.25)
    assert unit.pos == before, "Deadline branch must not teleport the unit"
    assert unit.current_order.kind == "MOVE"
    assert unit.current_order.params["destination"] == [1496, 900]
    assert [o.order_id for o in unit.order_queue] == ["B-NAV-EAST-FLANK"]


def test_destroyed_tdg3_unit_does_not_execute_queued_movement(tdg3_sim):
    sim = tdg3_sim
    unit = isolate(sim, "B-AT-1")
    for element in unit.elements.values():
        element.count = 0
    before = unit.pos
    for _ in range(20):
        sim.tick(.25)
    assert not unit.alive and unit.pos == before
    assert not any(r["kind"] == "ORDER_START" and r.get("unit") == unit.uid for r in sim.logs)


def test_same_seed_reproduces_initial_sensing_and_combat(tdg3_load):
    traces = []
    for _ in range(2):
        sim = tdg3_load()
        # Use actual TDG3 types and terrain in a deliberately visible corridor.
        # This probe isolates random sensing/fire, not the authored opening COA.
        keep = ("B-SEC-A", "R-T55-1", "R-T55-2")
        sim.units = {uid: sim.units[uid] for uid in keep}
        for unit in sim.units.values():
            unit.order_queue.clear()
            unit.current_order = None
        sim.units["B-SEC-A"].pos = (1316, 720)
        sim.units["R-T55-1"].pos = (1316, 600)
        sim.units["R-T55-2"].pos = (1316, 540)
        sim.paused, sim.speed = False, 1.0
        for _ in range(240):
            sim.tick(.25)
        traces.append([r for r in sim.logs if r["kind"] in ("TRACK_UPDATE", "FIRE", "ELEMENT_LOSS")])
    assert any(r["kind"] == "FIRE" for r in traces[0]), "Repeatability probe did not exercise stochastic combat"
    assert traces[0] == traces[1]


@pytest.mark.tdg3_integration
def test_full_tdg3_execution_preserves_movement_ammo_and_command_invariants(tdg3_load, tmp_path):
    wall_start=time.perf_counter()
    initial, sim = tdg3_load(), tdg3_load()
    sim.paused, sim.speed = False, 1.0
    first_crossing, exit_times = {}, {}
    geometry_violations = []
    last_counts = {uid: (u.personnel, u.equipment) for uid, u in sim.units.items()}
    last_ammo = {(u.uid, e.eid, i): w.ammo_remaining for u in sim.units.values()
                 for e in u.elements.values() for i, w in enumerate(e.weapons)}
    last_totals = (sum(u.personnel for u in sim.units.values()), sum(u.equipment for u in sim.units.values()))
    initial_ammo = {}
    for u in sim.units.values():
        for e in u.elements.values():
            for w in e.weapons:
                if w.ammo_remaining >= 0:
                    initial_ammo[w.name] = initial_ammo.get(w.name, 0) + w.ammo_remaining
    for step in range(14400):
        old = {uid: (u.pos, u.alive) for uid, u in sim.units.items()}
        sim.tick(.25)
        for uid, unit in sim.units.items():
            if uid not in old:
                parent = unit.metadata.get("detached_from")
                assert parent in old and unit.metadata.get("actual_detached_vehicle"), ("Unexpected reinforcement", uid)
                assert unit.pos == sim.units[parent].pos, ("Detached vehicle appeared elsewhere", uid)
                previous, was_alive = unit.pos, unit.alive
                last_counts[uid] = (unit.personnel, unit.equipment)
            else:
                previous, was_alive = old[uid]
            point = unit.pos
            assert all(math.isfinite(v) for v in point), (uid, sim.time, point)
            assert 0 <= point[0] <= sim.world["width_m"] and 0 <= point[1] <= sim.world["height_m"], (uid, sim.time, point)
            # ROAD gain is at most 1.2 in this map; factor 2 allows posture effects.
            assert math.dist(previous, point) <= unit.unit_type.max_speed_mps * 2 * .25 + 1e-6, ("teleport/speed jump", uid, sim.time)
            if not was_alive:
                assert point == previous, ("destroyed unit moved", uid)
            assert unit.personnel <= last_counts[uid][0] and unit.equipment <= last_counts[uid][1], ("unexplained replacement", uid)
            last_counts[uid] = (unit.personnel, unit.equipment)
            for e in unit.elements.values():
                assert 0 <= e.count <= e.initial_count, (uid, e.eid)
                for i, w in enumerate(e.weapons):
                    key = (uid, e.eid, i)
                    if last_ammo.get(key, w.ammo_capacity) >= 0:
                        assert 0 <= w.ammo_remaining <= last_ammo.get(key, w.ammo_capacity), key
                    last_ammo[key] = w.ammo_remaining
            if unit.unit_type.metadata["mobility_class"] in ("TRACKED", "WHEELED"):
                if previous != point and not sim.terrain.segment_passable(unit,previous,point):
                    geometry_violations.append({"unit":uid,"t":sim.time,"check":"continuous",
                                                "segment_start":previous,"segment_end":point})
                # Check the midpoint too so one time step cannot jump over a bank.
                for sample in (previous, tuple((a+b)/2 for a, b in zip(previous, point)), point):
                    if not sim.terrain.passable(unit, sample):
                        geometry_violations.append({"unit": uid, "t": sim.time, "sample": sample,
                                                    "segment_start": previous, "segment_end": point})
                    if sim.terrain.river_at(sample):
                        assert sim.terrain.on_bridge(sample), ("vehicle forded stream", uid, sim.time)
                        first_crossing.setdefault(uid, sim.time)
            if uid.startswith("R-T55") and math.dist(point, tuple(sim.objectives["SOUTH_EXIT_PROXY"])) <= 60:
                exit_times.setdefault(uid, sim.time)
        totals = (sum(u.personnel for u in sim.units.values()), sum(u.equipment for u in sim.units.values()))
        assert all(now <= old_total for now, old_total in zip(totals, last_totals)), "Detachment duplicated people/equipment"
        last_totals = totals
        current_ammo = {}
        for unit in sim.units.values():
            for e in unit.elements.values():
                for w in e.weapons:
                    if w.ammo_remaining >= 0:
                        current_ammo[w.name] = current_ammo.get(w.name, 0) + w.ammo_remaining
        assert all(amount <= initial_ammo[name] for name, amount in current_ammo.items()), "Detachment duplicated ammunition"
        initial_ammo = current_ammo
        if (step + 1) % 2400 == 0:
            print(f"TDG3 integration: {sim.time:.0f}/3600 simulated seconds", flush=True)
    report = audit_events(sim.logs, initial)
    report.update(duration_s=sim.time, step_s=.25, wall_time_s=time.perf_counter()-wall_start,
                  scenario_seed=json.loads((ROOT / "scenarios/tdg3.json").read_text(encoding="utf-8"))["seed"],
                  runtime={"python": platform.python_version(), "platform": platform.platform(),
                           "python_hash_seed": os.environ.get("PYTHONHASHSEED", "unset")},
                  first_on_bridge_s=first_crossing, south_exit_within_60m_s=exit_times,
                  geometry_violation_count=len(geometry_violations), geometry_violations=geometry_violations[:30],
                  final_personnel={side: sum(u.personnel for u in sim.units.values() if u.side.value == side)
                                   for side in ("BLUE", "RED")},
                  sha256={name: hashlib.sha256((ROOT / "scenarios" / name).read_bytes()).hexdigest()
                          for name in ("tdg3.json", "tdg3_blue_bml.json", "tdg3_red_bml.json", "tdg3_terrain.json")})
    path = tmp_path / "tdg3_outcome.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"TDG3 outcome report: {path}\n{json.dumps(report, indent=2)}")
    # Exercise real phase transitions without requiring a predetermined winner.
    assert any(r["kind"] == "DEADLINE_BRANCH" and r.get("unit") == "B-NAV" for r in sim.logs)
    assert any(r["kind"] == "ORDER_COMPLETE" and r.get("order_id") == "B-SEC-B-LOST-COMMS" for r in sim.logs)
    assert any(r["kind"] == "FIRE" for r in sim.logs), "The baseline never exercised combat"
    assert not geometry_violations, f"{len(geometry_violations)} blocked-terrain samples; first={geometry_violations[:1]}; report={path}"
