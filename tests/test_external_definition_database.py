import csv, json
from pathlib import Path

from mnsim.database import WeaponCatalog, LoadoutCatalog
from mnsim.definitions import default_definition_registry
from mnsim.scenario import load_scenario


def test_weapon_catalog_resolves_csv_definition():
    catalog = WeaponCatalog.from_csv('database/weapons.csv')
    reg = default_definition_registry(catalog)
    w = reg.create_weapon({'weapon_id':'ATGM_GENERIC', 'slot':'AT_WEAPON'})
    assert w.name == 'modern guided ATGM (generic)'
    assert w.ammo_capacity == 3
    assert w.metadata['weapon_id'] == 'ATGM_GENERIC'
    assert w.metadata['loadout_slot'] == 'AT_WEAPON'


def test_same_unit_type_can_use_different_per_unit_loadouts(tmp_path):
    # Clone current DB and add one deliberately distinct test rifle. This checks architecture,
    # not realism; no production combat constants are changed.
    rows = list(csv.DictReader(open('database/weapons.csv', encoding='utf-8-sig')))
    extra = dict(rows[0])
    extra.update({'weapon_id':'TEST_RIFLE_ALT','name':'test alternate rifle','range_m':'777'})
    db = tmp_path/'weapons.csv'
    with db.open('w',encoding='utf-8-sig',newline='') as f:
        wr=csv.DictWriter(f,fieldnames=rows[0].keys()); wr.writeheader(); wr.writerows(rows+[extra])
    loads=tmp_path/'loadouts.json'
    loads.write_text(json.dumps({'loadouts':{
        'BASE':{'slots':{'PRIMARY_RIFLE':'SMALL_ARMS_GENERIC'}},
        'ALT':{'slots':{'PRIMARY_RIFLE':'TEST_RIFLE_ALT'}},
    }}))
    toe = Path('config/toe_templates.json').resolve()
    scenario={
      'seed':7, 'world':{'width_m':1000,'height_m':1000},
      'unit_types_file':str(toe), 'weapon_database_file':str(db), 'loadouts_file':str(loads),
      'units':[
        {'id':'B1','side':'BLUE','type':'INF_PLT','echelon':'PLT','pos':[100,100],'loadout':'BASE'},
        {'id':'B2','side':'BLUE','type':'INF_PLT','echelon':'PLT','pos':[200,100],'loadout':'ALT'},
      ]
    }
    sp=tmp_path/'scenario.json'; sp.write_text(json.dumps(scenario))
    sim=load_scenario(str(sp), bml_files={})
    b1=sim.units['B1'].elements['rifle_1'].weapons[0]
    b2=sim.units['B2'].elements['rifle_1'].weapons[0]
    assert b1.name == 'small arms'
    assert b2.name == 'test alternate rifle'
    assert b1.range_m == 350
    assert b2.range_m == 777
