from mnsim.terrain import TerrainModel


def test_dense_forest_direct_fire_penetration_is_ray_based():
    t=TerrainModel({"areas":[{"type":"FOREST","polygon":[[0,0],[100,0],[100,100],[0,100]]}]})
    edge=t.direct_fire_modifier((90,50),(200,50))
    deep=t.direct_fire_modifier((-10,50),(60,50))
    assert edge["allowed"] is True
    assert 0.0 < edge["effect_factor"] < 1.0
    assert deep["allowed"] is False
    assert deep["effect_factor"] == 0.0


def test_woods_has_longer_direct_fire_penetration_than_forest():
    poly=[[0,0],[100,0],[100,100],[0,100]]
    forest=TerrainModel({"areas":[{"type":"FOREST","polygon":poly}]})
    woods=TerrainModel({"areas":[{"type":"WOODS","polygon":poly}]})
    ray=((-10,50),(70,50))
    assert forest.direct_fire_modifier(*ray)["allowed"] is False
    assert woods.direct_fire_modifier(*ray)["allowed"] is True
