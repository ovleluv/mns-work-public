from types import SimpleNamespace
from mnsim.terrain import TerrainModel


def fake_unit(mobility="FOOT",echelon="PLT",uid="U",branch="INFANTRY"):
    ut=SimpleNamespace(metadata={"mobility_class":mobility,"terrain_speed_factors":{}},max_speed_mps=2.0,branch=branch)
    return SimpleNamespace(uid=uid,echelon=echelon,unit_type=ut,pos=(0.0,0.0),alive=True,elements={})

def test_river_mobility_overrides_allow_foot_and_block_tracked():
    t=TerrainModel({"rivers":[{"id":"R","points":[[0,0],[100,0]],"width_m":20,
                               "mobility_overrides":{"FOOT":0.35,"TRACKED":0.0}}]})
    foot=fake_unit("FOOT")
    tank=fake_unit("TRACKED",branch="ARMOR")
    assert t.river_at((50,0))["id"]=="R"
    assert t.passable(foot,(50,0))
    assert not t.passable(tank,(50,0))
    assert abs(t.speed_factor(foot,(50,0))-0.35) < 1e-9

def test_river_without_override_keeps_default_water_crossing_rule():
    t=TerrainModel({"rivers":[{"id":"R","points":[[0,0],[100,0]],"width_m":20}]})
    foot=fake_unit("FOOT")
    amphibious=fake_unit("TRACKED",branch="ARMOR")
    amphibious.unit_type.metadata["mobility_capabilities"]=["WATER_CROSSING"]
    assert not t.passable(foot,(50,0))
    assert t.passable(amphibious,(50,0))

def test_lake_foot_passable_vehicle_blocked():
    t=TerrainModel({"areas":[{"id":"L","type":"LAKE","polygon":[[0,0],[100,0],[100,100],[0,100]],"mobility_overrides":{"FOOT":.16,"WHEELED":0},"impassable_mobility_classes":["WHEELED"]}]})
    assert t.passable(fake_unit("FOOT"),(50,50))
    assert not t.passable(fake_unit("WHEELED",branch="ARMOR"),(50,50))

def test_building_occupancy_is_unlimited_at_engine_level():
    b={"id":"B","type":"BUILDING","polygon":[[0,0],[100,0],[100,100],[0,100]],"capacity_platoons":0.1}
    t=TerrainModel({"areas":[b]})
    a=fake_unit("FOOT","BN","A");a.pos=(50,50)
    c=fake_unit("FOOT","COY","C");c.pos=(50,50)
    t._units_provider=lambda:[a,c]
    newcomer=fake_unit("FOOT","BN","N")
    assert t.building_has_capacity(newcomer,b)

def test_operational_building_blocks_ordinary_movement_but_explicit_access_allows_foot():
    b={"id":"B","type":"BUILDING","polygon":[[40,40],[60,40],[60,60],[40,60]]}
    t=TerrainModel({"areas":[b]})
    foot=fake_unit("FOOT"); foot.metadata={}
    assert not t.passable(foot,(50,50))
    foot.metadata["_building_access_id"]="B"
    assert t.passable(foot,(50,50))
    vehicle=fake_unit("WHEELED",branch="ARMOR"); vehicle.metadata={"_building_access_id":"B"}
    assert not t.passable(vehicle,(50,50))

def test_elevation_can_clear_low_building():
    data={"areas":[
      {"id":"H","type":"ELEVATION","polygon":[[-200,-100],[40,-100],[40,100],[-200,100]],"elevation_m":60,"transition_width_m":40},
      {"id":"B","type":"BUILDING","polygon":[[45,-10],[55,-10],[55,10],[45,10]],"height_m":8,"integrity":100,"max_integrity":100},
    ]}
    t=TerrainModel(data)
    assert t.elevation_at((0,0)) > 40
    high=t.observation_modifier((0,0),(100,0))
    assert high["detection_factor"] > 0.0

def test_elevation_can_see_over_intervening_forest_but_not_target_inside_it():
    data={"areas":[
      {"id":"H","type":"ELEVATION","polygon":[[-200,-100],[40,-100],[40,100],[-200,100]],"elevation_m":80,"transition_width_m":40},
      {"id":"F","type":"FOREST","polygon":[[45,-30],[75,-30],[75,30],[45,30]],"max_visual_penetration_m":10,"obstacle_height_m":18},
    ]}
    t=TerrainModel(data)
    assert t.observation_modifier((0,0),(120,0))["detection_factor"] > 0
    assert t.observation_modifier((0,0),(60,0))["detection_factor"] == 0

def test_building_damage_destroyed():
    b={"id":"B","type":"BUILDING","polygon":[[0,0],[20,0],[20,20],[0,20]],"integrity":20,"max_integrity":20}
    t=TerrainModel({"areas":[b]})
    rec=t.apply_building_damage(b,25,weapon="AT4")
    assert rec["destroyed"] and b["destroyed"]
