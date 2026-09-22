import json
from pathlib import Path
from mnsim.terrain import TerrainModel

ROOT=Path(__file__).resolve().parents[1]

def test_meandering_polyline_river_is_water_and_banks_are_not():
    tm=TerrainModel(json.loads((ROOT/"config/terrain_demo.json").read_text()))
    assert tm.in_river((900,2805))
    assert not tm.in_river((900,2600))

def test_authored_area_modifier_is_available_for_future_environment_use():
    tm=TerrainModel(json.loads((ROOT/"config/terrain_demo.json").read_text()))
    areas=tm.area_at((500,800))
    assert areas and areas[0]["type"]=="WOODS"
    mod=tm.observation_modifier((500,800),(700,800),"VISUAL")
    assert mod["detection_factor"] < 1.0

def test_rotated_bridge_geometry_supported():
    data={"rivers":[],"roads":[],"bridges":[{"id":"X","center":[100,100],"width_m":20,"length_m":100,"heading_deg":45}]}
    tm=TerrainModel(data)
    assert tm.on_bridge((100,100))
    assert not tm.on_bridge((30,100))
