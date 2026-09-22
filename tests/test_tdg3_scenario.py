"""Contracts for the checked-in TDG3 model, not assertions of historical fact.

35 BLUE and 27 RED dismounts, surviving B Security and enlarged terrain are
explicit baseline assumptions. Changing those assumptions should update these
tests deliberately; no test requires a particular side to win.
"""
import copy
import math
from pathlib import Path

import pytest

from mnsim.bml import ConditionEvaluator, compile_order_fragment


ROOT = Path(__file__).resolve().parents[1]


def walk_orders(sim, unit, orders):
    for order in orders:
        yield order
        for fragment in (order.on_true, order.on_false, order.on_deadline):
            if fragment:
                yield from walk_orders(sim, unit, [compile_order_fragment(sim, unit, fragment)])


def test_tdg3_force_accounting_preserves_charlie_and_two_attached_launchers(tdg3_sim):
    sim = tdg3_sim
    assert sum(u.personnel for u in sim.units.values() if u.side.value == "BLUE") == 35
    assert sum(u.personnel for u in sim.units.values() if u.side.value == "RED") == 27
    assert sum(sim.units[uid].personnel for uid in ("B-SEC-A", "B-SEC-B", "B-SEC-C", "B-NAV")) == 8
    assert sim.units["B-ALPHA"].personnel == sim.units["B-BRAVO"].personnel == 8
    armor = [u for u in sim.units.values() if u.side.value == "RED" and u.branch == "ARMOR"]
    assert len(armor) == 4
    assert all(u.unit_type.name == "TDG3_T55" and u.equipment == 1 for u in armor)
    trucks = [sim.units[f"R-TRUCK-{i}"] for i in (1, 2)]
    assert all(u.equipment == 1 and not u.operational_weapons() for u in trucks)
    assert all(e.metadata.get("passengers", 0) == 0 for u in trucks for e in u.elements.values())
    launchers = [(u, e, w) for u in sim.units.values() if u.side.value == "BLUE"
                 for e, w in u.operational_weapons() if w.capability == "ANTI_ARMOR"]
    assert len(launchers) == 2
    assert sum(u.firepower(e,w).participants for u, e, w in launchers) == 2
    assert all(w.ammo_remaining == 3 for _, _, w in launchers)
    assert not any(w.capability == "INDIRECT_FIRE" for u in sim.units.values()
                   for e in u.elements.values() for w in e.weapons)


def test_tdg3_loads_night_and_keeps_mutable_weapon_inventory_separate(tdg3_sim):
    sim = tdg3_sim
    assert sim.combat_config["environment"]["illumination"] == "NIGHT"
    assert sim.combat_config["environment"]["weather"] == "CLEAR"
    first = next(w for _, w in sim.units["B-AT-1"].operational_weapons() if w.capability == "ANTI_ARMOR")
    other = next(w for _, w in sim.units["B-AT-2"].operational_weapons() if w.capability == "ANTI_ARMOR")
    for expected in (2, 1, 0):
        assert first.expend_round()
        assert first.ammo_remaining == expected
    assert not first.expend_round()
    assert other.ammo_remaining == 3
    assert not any(w.capability == "ANTI_ARMOR" for _, w in sim.units["B-AT-1"].operational_weapons())


def test_tdg3_bml_replaces_only_named_units_and_no_bml_keeps_standing_orders(tdg3_load):
    baseline, planned = tdg3_load(False), tdg3_load(True)
    lost_orders = [o.order_id for o in baseline.units["B-SEC-B"].order_queue]
    assert [o.order_id for o in planned.units["B-SEC-B"].order_queue] == lost_orders
    assert [o.kind for o in planned.units["B-SEC-B"].order_queue] == ["HOLD", "MOVE", "MOVE", "MOVE", "HOLD"]
    assert planned.units["B-SEC-B"].order_queue[0].params["duration_s"] == 180
    for uid, u in baseline.units.items():
        if uid == "B-SEC-B":
            continue
        assert [o.kind for o in u.order_queue] == ["HOLD"]
        assert not set(o.order_id for o in u.order_queue) & set(o.order_id for o in planned.units[uid].order_queue)


def test_tdg3_all_spawns_orders_and_branches_are_valid_without_enemy_ground_truth(tdg3_sim):
    sim = tdg3_sim
    for unit in sim.units.values():
        assert sim.terrain.passable(unit, unit.pos), (unit.uid, unit.pos)
        assert unit.order_queue
        for order in walk_orders(sim, unit, unit.order_queue):
            # This COA uses terrain objectives, not omniscient target coordinates/counts.
            assert "target_unit" not in order.params
            assert "search_reference" not in order.params
            for condition in order.conditions:
                assert condition["lhs"] != "self.enemy_count_near"
                ConditionEvaluator.eval_one(sim, unit, condition)
            for key in ("destination", "center"):
                if order.params.get(key) is None:
                    continue
                point = tuple(order.params[key])
                assert all(math.isfinite(v) for v in point)
                assert 0 <= point[0] <= sim.world["width_m"]
                assert 0 <= point[1] <= sim.world["height_m"]
                assert sim.terrain.passable(unit, point), (unit.uid, order.order_id, point)
    # Repeated branch fragments are intentional; authored top-level orders are unique.
    top = [o.order_id for u in sim.units.values() for o in u.order_queue]
    assert len(top) == len(set(top))


@pytest.mark.parametrize("uid", ["R-T55-1", "R-TRUCK-1"])
def test_tdg3_vehicle_requires_intact_bridge_but_foot_can_ford(tdg3_sim, uid):
    terrain = tdg3_sim.terrain
    vehicle, foot = tdg3_sim.units[uid], tdg3_sim.units["B-SEC-B"]
    bridge = terrain.bridges[0]
    crossing = terrain.bridge_center(bridge)
    ford = tuple(terrain.rivers[0]["points"][10])
    assert terrain.river_at(ford) and not terrain.on_bridge(ford)
    assert terrain.passable(foot, ford)
    assert terrain.speed_factor(foot, ford) > 0
    assert not terrain.passable(vehicle, ford)
    assert terrain.passable(vehicle, crossing)
    assert terrain.speed_factor(vehicle, crossing) > 0
    # The road crosses the river as well; a road alone must not act as a bridge.
    assert terrain.on_road(crossing)
    bridge.update(destroyed=True, integrity=0)
    assert not terrain.passable(vehicle, crossing)
    assert terrain.passable(foot, crossing)
    assert not terrain.passable(vehicle, (200, 200))
    assert terrain.passable(foot, (200, 200))


def test_tdg3_bridge_has_no_blocked_gap_between_road_approaches(tdg3_sim):
    sim = tdg3_sim
    tank = sim.units["R-T55-1"]
    approach = next(o.params["destination"] for o in tank.order_queue if o.order_id.endswith("CROSSING-APPROACH"))
    exit_point = next(o.params["destination"] for o in tank.order_queue if o.order_id.endswith("CROSS-BRIDGE"))
    for i in range(201):
        point = tuple(approach[k] + (exit_point[k] - approach[k]) * i / 200 for k in (0, 1))
        assert sim.terrain.passable(tank, point), point
        assert sim.terrain.speed_factor(tank, point) > 0, point
        if sim.terrain.river_at(point):
            assert sim.terrain.on_bridge(point), point


def test_tdg3_editor_roundtrip_preserves_units_and_terrain(tmp_path):
    from editor import Editor
    editor = Editor(str(ROOT / "scenarios/tdg3.json"))
    scenario, terrain = copy.deepcopy(editor.scenario), copy.deepcopy(editor.terrain)
    editor.scenario_path = tmp_path / "tdg3.json"
    editor.terrain_path = tmp_path / "tdg3_terrain.json"
    editor.save()
    restored = Editor(str(editor.scenario_path))
    assert restored.scenario == scenario
    assert restored.terrain == terrain
    assert restored.world_size() == (2016, 2592)
