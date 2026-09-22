"""Regressions for the continuous-collision gap found in the TDG3 experiment."""
import pytest

from mnsim.model import UnitState
from mnsim.terrain import TerrainModel


def test_vehicle_step_cannot_jump_a_thin_impassable_forest(tdg3_sim):
    sim = tdg3_sim
    tank = sim.units["R-T55-1"]
    # 0.4 m strip between two passable endpoints of an ordinary 0.25 s step.
    sim.terrain = TerrainModel({"areas": [{
        "id": "THIN_FOREST", "type": "FOREST",
        "polygon": [[.8, -10], [1.2, -10], [1.2, 10], [.8, 10]],
        "impassable_mobility_classes": ["TRACKED"]
    }]})
    tank.pos, tank.state = (0, 0), UnitState.MOVING
    assert sim.terrain.passable(tank, tank.pos)
    assert not sim.terrain.passable(tank, (1, 0))
    sim._move_toward(tank, (6, 0), .25)
    assert tank.pos[0] <= .8, "Runtime skipped a blocked interval between passable endpoints"


@pytest.mark.parametrize("a,b", [
    ((1035.6,1947.1),(1036.3,1948.8)),
    ((1035.8727251174023,1947.2475706537743),(1036.1032613168977,1948.71709755408)),
])
def test_tdg3_planner_rejects_a_segment_clipping_actual_forest_corner(tdg3_sim,a,b):
    terrain = tdg3_sim.terrain
    tank = tdg3_sim.units["R-T55-3"]
    midpoint = tuple((x+y)/2 for x,y in zip(a,b))
    assert terrain.passable(tank, a) and terrain.passable(tank, b)
    assert not terrain.passable(tank, midpoint)
    assert not terrain.navigation.segment_passable(tank, a, b)
