import json
from pathlib import Path
from mnsim.scenario import load_scenario

ROOT=Path(__file__).resolve().parents[1]

def test_individual_squad_and_mech_templates_exist():
    raw=json.loads((ROOT/'config/toe_templates.json').read_text())['unit_types']
    assert raw['INF_IND']['metadata']['echelon']=='IND'
    assert raw['US_RIFLE_SQD']['metadata']['echelon']=='SQD'
    assert raw['US_MECH_INF_PLT_BRADLEY']['branch']=='MECH_INFANTRY'
    assert raw['BMP_MECH_INF_PLT']['branch']=='MECH_INFANTRY'
    assert raw['ROK_MECH_INF_PLT_K200']['branch']=='MECH_INFANTRY'
    assert raw['US_MOT_INF_PLT_HMMWV']['branch']=='MOTORIZED_INFANTRY'

def test_explicit_machine_gun_count_and_crew_limit_runtime(tmp_path):
    src=json.loads((ROOT/'scenarios/tdg1.json').read_text())
    src['units']=[{'id':'B-MG','name':'B-MG','side':'BLUE','echelon':'SQD','type':'MG_SQD','pos':[100,100],
                   'element_overrides':[{'id':'mg_crew','count':5,'initial_count':5,'weapon_system_counts':{'GPMG_SINGLE_GENERIC':3},'weapon_operators_per_system':{'GPMG_SINGLE_GENERIC':3}}]}]
    src.pop('terrain_file',None); src.pop('bml_files',None); src.pop('blue_bml_file',None); src.pop('red_bml_file',None)
    # references relative to temp scenario
    src['unit_types_file']=str((ROOT/'config/toe_templates.json').resolve())
    src['weapon_database_file']=str((ROOT/'database/weapons.csv').resolve())
    src['loadouts_file']=str((ROOT/'database/loadouts.json').resolve())
    src['config_file']=str((ROOT/'config/defaults.json').resolve())
    src['artillery_doctrine_file']=str((ROOT/'config/artillery_doctrine.json').resolve())
    src['targeting_doctrine_file']=str((ROOT/'config/targeting_doctrine.json').resolve())
    p=tmp_path/'s.json'; p.write_text(json.dumps(src))
    sim=load_scenario(str(p),bml_files={}); e=sim.units['B-MG'].elements['mg_crew']; w=e.weapons[0]
    assert w.metadata['system_count']==3
    assert w.metadata['operators_per_system']==3
    assert sim.units['B-MG'].firepower(e,w).participants==1
