from mnsim.terrain import TerrainModel


def test_polyline_bridge_geometry_and_center():
    br={"id":"PB","points":[[100,100],[100,200],[200,200]],"width_m":20,"structural_integrity":100}
    t=TerrainModel({"roads":[],"rivers":[],"areas":[],"bridges":[br]})
    assert t.on_bridge((104,150))
    assert t.on_bridge((150,196))
    assert not t.on_bridge((130,150))
    assert t.bridge_center(br)==(100.0,200.0)


def test_legacy_bridge_geometry_still_supported():
    br={"id":"L","center":[100,100],"width_m":20,"length_m":100,"heading_deg":0}
    t=TerrainModel({"roads":[],"rivers":[],"areas":[],"bridges":[br]})
    assert t.on_bridge((140,100))
    assert not t.on_bridge((100,120))
