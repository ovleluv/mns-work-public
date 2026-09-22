import json
from pathlib import Path
from mnsim.scenario import load_scenario
from mnsim.bml import compile_mission

ROOT=Path(__file__).resolve().parents[1]

def test_attack_structure_bml_compiles(tmp_path):
    terr=tmp_path/'t.json'; terr.write_text(json.dumps({"roads":[],"rivers":[],"bridges":[],"areas":[{"id":"BLD1","type":"BUILDING","polygon":[[100,100],[140,100],[140,140],[100,140]],"integrity":20,"max_integrity":20}]}))
    src=json.loads((ROOT/'scenarios/test2.json').read_text())
    src['units']=[src['units'][1]]
    src['terrain_file']='t.json'; src['config_file']=str((ROOT/'config/defaults.json').resolve()); src['unit_types_file']=str((ROOT/'config/toe_templates.json').resolve());src['artillery_doctrine_file']=str((ROOT/'config/artillery_doctrine.json').resolve());src['targeting_doctrine_file']=str((ROOT/'config/targeting_doctrine.json').resolve())
    sp=tmp_path/'s.json';sp.write_text(json.dumps(src))
    sim=load_scenario(str(sp))
    uid,o=compile_mission(sim,{"unit":"B-INF_PLT-1","task":"ATTACK_STRUCTURE","target_structure":"BLD1"})
    assert uid=='B-INF_PLT-1' and o.kind=='ATTACK_STRUCTURE' and o.params['target_structure']=='BLD1'

def test_elevated_direct_fire_clears_intervening_building():
    from mnsim.terrain import TerrainModel
    data={"areas":[
      {"id":"H","type":"ELEVATION","polygon":[[-200,-100],[40,-100],[40,100],[-200,100]],"elevation_m":80,"transition_width_m":40},
      {"id":"B","type":"BUILDING","polygon":[[45,-10],[55,-10],[55,10],[45,10]],"height_m":8,"integrity":100,"max_integrity":100},
    ]}
    t=TerrainModel(data)
    assert t.direct_fire_modifier((0,0),(100,0))["allowed"]
