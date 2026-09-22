import json
from pathlib import Path
from mnsim.scenario import load_scenario
from mnsim.terrain import TerrainModel

ROOT=Path(__file__).resolve().parents[1]

def test_dense_forest_blocks_vehicle_offroad_and_slows_foot():
    sim=load_scenario(str(ROOT/"scenarios/demo.json"))
    forest=(1750,700)
    inf=sim.units["B-INF-2"]
    tank=sim.units["B-TK-1"]
    arty=sim.units["B-ART-1"]
    assert any(a["type"]=="FOREST" for a in sim.terrain.area_at(forest))
    assert sim.terrain.passable(inf,forest)
    assert not sim.terrain.passable(tank,forest)
    assert not sim.terrain.passable(arty,forest)
    # Compare same unit in open ground; forest must materially slow dismounted movement.
    assert sim.terrain.speed_factor(inf,forest) < 0.60 * sim.terrain.speed_factor(inf,(2500,500))

def test_dense_forest_visual_penetration_limit():
    tm=TerrainModel({
        "areas":[{
            "id":"F","type":"FOREST","polygon":[[100,0],[500,0],[500,500],[100,500]],
            "observation_modifier":{"range_factor":0.9,"fov_factor":0.8,"awareness_factor":0.75,"detection_factor":0.55},
            "max_visual_penetration_m":100.0,
            "sensor_penetration_m":{"VISUAL":100.0,"THERMAL":120.0}
        }]
    })
    # Target 60 m inside the near forest edge remains potentially detectable.
    near=tm.observation_modifier((0,250),(160,250),"VISUAL")
    assert near["detection_factor"] > 0.0
    # Target 180 m inside exceeds the authored penetration depth.
    deep=tm.observation_modifier((0,250),(280,250),"VISUAL")
    assert deep["detection_factor"] == 0.0
    assert deep["range_factor"] == 0.0

def test_road_is_vehicle_corridor_through_dense_forest():
    sim=load_scenario(str(ROOT/"scenarios/demo.json"))
    tank=sim.units["B-TK-1"]
    tm=TerrainModel({
        "roads":[{"id":"R","width_m":30,"points":[[0,100],[400,100]]}],
        "areas":[{"id":"F","type":"FOREST","polygon":[[50,0],[350,0],[350,200],[50,200]],
                  "mobility_overrides":{"FOOT":0.45,"TRACKED":0.0,"WHEELED":0.0},
                  "impassable_mobility_classes":["TRACKED","WHEELED"]}]
    })
    assert tm.passable(tank,(200,100))
    assert not tm.passable(tank,(200,160))
