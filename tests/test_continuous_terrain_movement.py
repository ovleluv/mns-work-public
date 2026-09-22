"""Hard-terrain segment contracts, independent of combat outcomes."""
import math

import pytest

from mnsim.model import UnitState
from mnsim.terrain import TerrainModel


def forest(poly=None):
    return {"id":"F", "type":"FOREST",
            "polygon":poly or [[8,-10],[12,-10],[12,10],[8,10]],
            "impassable_mobility_classes":["TRACKED","WHEELED"]}


@pytest.mark.parametrize("reverse", [False, True])
def test_forest_road_exception_requires_continuous_road_coverage(tdg3_sim, reverse):
    tank=tdg3_sim.units["R-T55-1"]
    terrain=TerrainModel({"areas":[forest()],
                          "roads":[{"points":[[0,0],[20,0]],"width_m":2}]})
    a,b=((20,0),(0,0)) if reverse else ((0,0),(20,0))
    assert terrain.segment_passable(tank,a,b)
    terrain.roads[:]=[{"points":[[0,0],[9,0]],"width_m":2},
                      {"points":[[11.2,0],[20,0]],"width_m":2}]
    # Round caps end at x=10 and begin at x=10.2. Endpoints remain legal.
    assert not terrain.segment_passable(tank,a,b)


@pytest.mark.parametrize("polygon", [False, True])
def test_thin_water_requires_live_bridge_for_vehicle_but_allows_foot(tdg3_sim, polygon):
    river={"mobility_overrides":{"FOOT":.5,"TRACKED":0}}
    river.update({"polygon":[[-10,-.2],[10,-.2],[10,.2],[-10,.2]]} if polygon
                 else {"points":[[-10,0],[10,0]],"width_m":.4})
    terrain=TerrainModel({"rivers":[river],"roads":[{"points":[[0,-10],[0,10]],"width_m":2}],
                          "bridges":[{"id":"BR","points":[[0,-1],[0,1]],"width_m":2}]})
    tank,foot=tdg3_sim.units["R-T55-1"],tdg3_sim.units["B-SEC-B"]
    a,b=(0,-5),(0,5)
    assert terrain.segment_passable(tank,a,b)
    terrain.bridges[0]["destroyed"]=True
    assert not terrain.segment_passable(tank,a,b)
    assert terrain.segment_passable(foot,a,b)
    assert not terrain.segment_passable(tank,b,a)


@pytest.mark.parametrize("a,b,allowed", [
    ((0,10),(20,10),False),  # Collinear boundary overlap is blocked.
    ((0,2),(16,18),False),   # Tangent contact with the corner (8,10).
    ((0,10.001),(20,10.001),True),
    ((7.999,0),(8.001,0),False),
    ((8,0),(8,0),False),
    ((0,0),(0,0),True),
])
def test_polygon_contact_short_and_zero_segments_are_direction_independent(tdg3_sim,a,b,allowed):
    terrain=TerrainModel({"areas":[forest()]})
    tank=tdg3_sim.units["R-T55-1"]
    assert terrain.segment_passable(tank,a,b) is allowed
    assert terrain.segment_passable(tank,b,a) is allowed
    assert terrain.navigation.segment_passable(tank,a,b) is allowed
    if a==b:
        assert len(terrain.navigation.plan(tank,a,b))==(2 if allowed else 1)


@pytest.mark.parametrize("dt", [.25, 1, 20])
def test_runtime_guards_entire_step_even_when_waypoint_provider_is_stale(tdg3_sim,monkeypatch,dt):
    sim=tdg3_sim; tank=sim.units["R-T55-1"]
    sim.terrain=TerrainModel({"areas":[forest([[.8,-10],[1.2,-10],[1.2,10],[.8,10]])]})
    tank.pos=(0,0); tank.state=UnitState.MOVING
    tank.metadata["_nav_route"]=[[0,0],[30,0]]
    monkeypatch.setattr(sim.terrain,"movement_target",lambda unit,dest:dest)
    sim._move_toward(tank,(30,0),dt)
    assert tank.pos==(0,0)
    assert "_nav_route" not in tank.metadata


@pytest.mark.parametrize("poly", [
    [[20,-10],[50,-10],[50,10],[20,10]],
    [[20,-20],[50,-20],[50,0],[35,0],[35,20],[20,20]],
])
@pytest.mark.parametrize("clockwise", [False, True])
def test_vehicle_routes_around_convex_and_concave_forest_without_stalling(tdg3_sim,poly,clockwise):
    if clockwise:poly=list(reversed(poly))
    sim=tdg3_sim; tank=sim.units["R-T55-1"]
    sim.terrain=TerrainModel({"areas":[forest(poly)]})
    tank.pos=(0,0); tank.state=UnitState.MOVING
    destination=(80,0)
    route=sim.terrain.plan_route(tank,destination)
    assert len(route)>2
    assert all(sim.terrain.segment_passable(tank,a,b) for a,b in zip(route,route[1:]))
    for _ in range(300):
        previous=tank.pos
        reached=sim._move_toward(tank,destination,.25)
        assert sim.terrain.segment_passable(tank,previous,tank.pos)
        if reached:break
    assert math.dist(tank.pos,destination)<=3


def test_short_direct_route_cannot_bypass_planner_check(tdg3_sim):
    terrain=TerrainModel({"areas":[forest([[.3,-10],[.4,-10],[.4,10],[.3,10]])]})
    tank=tdg3_sim.units["R-T55-1"]
    route=terrain.navigation.plan(tank,(0,0),(.8,0))
    assert len(route)>2
    assert all(terrain.segment_passable(tank,a,b) for a,b in zip(route,route[1:]))


def test_cached_route_is_replanned_after_new_blocking_area(tdg3_sim):
    terrain=TerrainModel({}, {"local_direct_route_m":0})
    tank=tdg3_sim.units["R-T55-1"]; tank.pos=(0,0)
    assert terrain.movement_target(tank,(80,0))==(80,0)
    terrain.areas.append(forest())
    waypoint=terrain.movement_target(tank,(80,0))
    assert waypoint!=(80,0)
    assert terrain.segment_passable(tank,tank.pos,waypoint)


def test_submillimetre_start_and_destination_are_not_deduplicated(tdg3_sim):
    terrain=TerrainModel({})
    tank=tdg3_sim.units["R-T55-1"]
    assert terrain.navigation.plan(tank,(0,0),(.0001,0))==[(0,0),(.0001,0)]


def test_fallback_portals_cannot_escape_world_to_bypass_full_width_obstacle(tdg3_sim):
    terrain=TerrainModel({"areas":[forest([[8,0],[12,0],[12,20],[8,20]])]})
    terrain.world={"width_m":20,"height_m":20}
    tank=tdg3_sim.units["R-T55-1"]
    assert terrain.navigation.plan(tank,(1,10),(19,10))==[(1.,10.)]


def test_road_round_cap_and_exact_road_edge_remain_traversable_in_forest(tdg3_sim):
    terrain=TerrainModel({"areas":[forest()],"roads":[{"points":[[0,0],[11,0]],"width_m":4}]})
    tank=tdg3_sim.units["R-T55-1"]
    assert terrain.segment_passable(tank,(0,0),(12,0))
    assert terrain.segment_passable(tank,(0,2),(11,2))
    assert not terrain.segment_passable(tank,(0,2.001),(11,2.001))


def test_unrestricted_fast_path_observes_changed_mobility_rules(tdg3_sim):
    terrain=TerrainModel({"areas":[forest()]})
    foot=tdg3_sim.units["B-SEC-B"]
    assert terrain.segment_passable(foot,(0,0),(20,0))
    terrain.areas[0]["mobility_overrides"]={"FOOT":0}
    assert not terrain.segment_passable(foot,(0,0),(20,0))


def test_authored_steep_elevation_keeps_planning_grade_limit(tdg3_sim):
    terrain=TerrainModel({"areas":[{"type":"ELEVATION","elevation_m":100,"transition_width_m":1,
                                    "polygon":[[10,0],[20,0],[20,10],[10,10]]}]})
    tank=tdg3_sim.units["R-T55-1"]
    a,b=(9,5),(12,5)
    assert terrain.segment_passable(tank,a,b)
    assert not terrain.navigation.segment_passable(tank,a,b)


def test_nearby_route_does_not_validate_all_distant_road_node_pairs(tdg3_sim,monkeypatch):
    points=[[1000+i*10,1000] for i in range(30)]
    terrain=TerrainModel({"roads":[{"points":points,"width_m":10}]})
    tank=tdg3_sim.units["R-T55-1"]
    checks=0
    original=terrain.navigation.segment_passable
    def counted(unit,a,b):
        nonlocal checks
        checks+=1
        return original(unit,a,b)
    monkeypatch.setattr(terrain.navigation,"segment_passable",counted)
    assert terrain.navigation.plan(tank,(0,0),(10,0))==[(0.,0.),(10.,0.)]
    # A deterministic work bound, not a machine-dependent timing assertion.
    # Eager all-pairs construction would perform 496 checks here.
    assert checks<100
