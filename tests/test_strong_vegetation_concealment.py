from types import SimpleNamespace
from mnsim.terrain import TerrainModel
from mnsim.environment import EnvironmentObservationModel


def _terrain(kind):
    return TerrainModel({"areas":[{"id":"V1","type":kind,"polygon":[[0,0],[300,0],[300,300],[0,300]]}]})

def test_forest_visual_path_blocks_after_75m():
    t=_terrain("FOREST")
    assert t.observation_modifier((10,150),(100,150),"VISUAL")["detection_factor"] == 0.0

def test_woods_visual_path_allows_150_but_blocks_200m():
    t=_terrain("WOODS")
    assert t.observation_modifier((10,150),(160,150),"VISUAL")["detection_factor"] > 0.0
    assert t.observation_modifier((10,150),(210,150),"VISUAL")["detection_factor"] == 0.0

def test_defending_infantry_gets_more_forest_concealment_than_moving():
    t=_terrain("FOREST")
    env=EnvironmentObservationModel({})
    obs=SimpleNamespace(pos=(290,150), branch="INFANTRY", state=SimpleNamespace(name="DEFENDING"))
    defending=SimpleNamespace(pos=(250,150), branch="INFANTRY", state=SimpleNamespace(name="DEFENDING"))
    moving=SimpleNamespace(pos=(250,150), branch="INFANTRY", state=SimpleNamespace(name="MOVING"))
    assert env.modifier(t,obs,defending).detection_factor < env.modifier(t,obs,moving).detection_factor
