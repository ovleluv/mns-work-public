import math

from mnsim.model import UnitState
from mnsim.scenario import load_scenario


def _assert_continuously_passable_route(sim, unit, destination):
    route = sim.terrain.plan_route(unit, destination)
    assert len(route) > 1
    assert math.dist(tuple(route[0]), tuple(unit.pos)) < 1e-6
    assert math.dist(tuple(route[-1]), tuple(destination)) < 1e-6
    for start, end in zip(route, route[1:]):
        assert sim.terrain.navigation.segment_passable(unit, tuple(start), tuple(end))
    return route


def test_unit_specific_routes_are_built_from_real_terrain_and_mobility():
    sim = load_scenario("scenarios/demo.json")

    infantry = sim.units["B-INF-2"]
    infantry.pos = (900.0, 1700.0)
    infantry_route = _assert_continuously_passable_route(sim, infantry, (1800.0, 1850.0))
    assert len(infantry_route) >= 2

    tank = sim.units["B-TK-1"]
    tank.pos = (2050.0, 2500.0)
    tank_route = _assert_continuously_passable_route(sim, tank, (2050.0, 3200.0))
    assert any(sim.terrain.on_bridge(tuple(point)) for point in tank_route[1:-1])

    artillery = sim.units["B-ART-1"]
    artillery.pos = (250.0, 1800.0)
    artillery_route = _assert_continuously_passable_route(sim, artillery, (3800.0, 2200.0))
    assert any(sim.terrain.on_road(tuple(point)) for point in artillery_route[1:-1])


def test_movement_steps_follow_waypoints_without_entering_uncrossable_water():
    sim = load_scenario("scenarios/demo.json")
    tank = sim.units["B-TK-1"]
    tank.order_queue.clear()
    tank.current_order = None
    tank.pos = (2050.0, 2500.0)
    tank.state = UnitState.MOVING
    destination = (2050.0, 3200.0)

    arrived = False
    for _ in range(900):
        arrived = sim._move_toward(tank, destination, 1.0)
        assert sim.terrain.passable(tank, tank.pos)
        assert sim.terrain.river_at(tank.pos) is None or sim.terrain.on_bridge(tank.pos)
        if arrived:
            break

    assert arrived
    assert math.dist(tank.pos, destination) <= 3.0


def test_cached_route_is_replanned_when_bridge_state_changes():
    sim = load_scenario("scenarios/demo.json")
    tank = sim.units["B-TK-1"]
    tank.pos = (2050.0, 1800.0)
    destination = (2050.0, 3500.0)

    sim.terrain.movement_target(tank, destination)
    initial_route = [tuple(point) for point in tank.metadata["_nav_route"]]
    assert sim.terrain.navigation.segment_passable(tank, initial_route[0], initial_route[-1])

    br1 = sim.terrain.bridge_by_id("BR1")
    br1["destroyed"] = True
    br1["integrity"] = 0.0

    sim.terrain.movement_target(tank, destination)
    replanned_route = [tuple(point) for point in tank.metadata["_nav_route"]]
    assert replanned_route != initial_route
    assert not tank.metadata.get("_nav_no_path")
    assert any(point[0] > 3000.0 for point in replanned_route[1:-1])
