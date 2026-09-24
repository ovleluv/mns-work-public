"""Fast geometry checks must retain authored sensor and movement behavior."""

import pytest

from mnsim.model import FormationElement, Side, Unit, UnitType
from mnsim.simulation import Simulation
from mnsim.terrain import TerrainModel


def _infantry(uid, side, pos, detection_range=500.0):
    person = FormationElement("rifle", "rifle", "PERSONNEL", "RIFLE", 1, 1)
    return Unit(uid, uid, side, "PLT", UnitType("infantry", "INFANTRY", 1.0,
                                               detection_range, metadata={"mobility_class": "FOOT"}),
                pos, elements={"rifle": person})


@pytest.mark.parametrize("target_pos,modifier", [
    ((800.0, 100.0), {"range_factor": 2.0}),
    ((350.0, 500.0), {"fov_factor": 2.0}),
    ((-200.0, 100.0), {"awareness_factor": 2.0}),
])
def test_sensor_prefilter_keeps_authored_geometry_boosts(target_pos, modifier):
    sim = Simulation(seed=7)
    sim.terrain = TerrainModel({"observation_zones": [{"type": "TEST",
        "polygon": [[-300, -300], [900, -300], [900, 600], [-300, 600]],
        "observation_modifier": modifier}]})
    observer = _infantry("B", Side.BLUE, (100.0, 100.0))
    target = _infantry("R", Side.RED, target_pos)
    sim.add_unit(observer)
    sim.add_unit(target)
    sim.rng.random = lambda: 0.0
    sim._sensor_step()
    assert target.uid in observer.local_tracks


def test_navigation_still_rejects_passable_region_with_zero_movement_speed():
    sim = Simulation()
    sim.terrain = TerrainModel({"areas": [{"type": "MUD",
        "polygon": [[40, -20], [60, -20], [60, 20], [40, 20]],
        "movement_factor": 0.0}]})
    unit = _infantry("B", Side.BLUE, (0.0, 0.0))
    assert sim.terrain.segment_passable(unit, (0.0, 0.0), (100.0, 0.0))
    assert not sim.terrain.navigation.segment_passable(unit, (0.0, 0.0), (100.0, 0.0))
