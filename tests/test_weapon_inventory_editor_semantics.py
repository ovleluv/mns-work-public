import csv, json
from pathlib import Path

from mnsim.scenario import load_scenario

ROOT=Path(__file__).resolve().parents[1]


def _weapon_metadata():
    out={}
    with (ROOT/'database/weapons.csv').open('r',encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            out[r['weapon_id']]=json.loads(r.get('metadata_json') or '{}')
    return out


def test_editor_distinguishes_disposable_crew_served_and_platform_mounts():
    md=_weapon_metadata()
    at4=md['AT4_MULTIROLE_GENERIC']
    assert at4['inventory_model']=='DISPOSABLE_ROUNDS'
    assert at4['editor_quantity_label']=='AT4 rounds'
    assert at4['editor_show_crew'] is False and at4['editor_show_ammo'] is False

    mg=md['GPMG_SINGLE_GENERIC']
    assert mg['inventory_model']=='CREW_SERVED' and mg['editor_show_crew'] is True

    cannon=md['IFV_CANNON_GENERIC']
    assert cannon['inventory_model']=='PLATFORM_MOUNT' and cannon['editor_show_crew'] is False
    assert cannon['editor_quantity_label']=='weapon mounts'

    source=(ROOT/'editor.py').read_text()
    assert "inv=='DISPOSABLE_ROUNDS'" in source
    assert "w.get('show_crew')" in source
    assert "crew / weapon" in source


def test_template_weapon_staffing_and_mount_counts_are_internally_possible():
    md=_weapon_metadata()
    raw=json.loads((ROOT/'config/toe_templates.json').read_text())['unit_types']
    problems=[]
    for uname,u in raw.items():
        for e in u.get('elements',[]):
            n=int(e.get('count',0)); cat=str(e.get('category','')).upper()
            for w in e.get('weapons',[]):
                wm=dict(md.get(w.get('weapon_id'),{})); wm.update(w.get('metadata',{}))
                inv=str(wm.get('inventory_model','')).upper()
                if 'system_count' not in wm: continue
                systems=max(0,int(wm['system_count']))
                if cat=='PERSONNEL' and n>0 and inv not in ('DISPOSABLE_ROUNDS','PERSONNEL_AGGREGATE'):
                    crew=max(1,int(wm.get('operators_per_system',1)))
                    if systems*crew>n:
                        problems.append((uname,e.get('id'),w.get('weapon_id'),'crew'))
                if cat=='EQUIPMENT' and inv=='PLATFORM_MOUNT':
                    spp=max(1,int(wm.get('systems_per_provider',1)))
                    if systems>n*spp:
                        problems.append((uname,e.get('id'),w.get('weapon_id'),'mount'))
    assert problems==[]


def test_disposable_rounds_need_no_system_or_operator_override(tmp_path):
    raw={
      'seed':2,'world':{'width_m':500,'height_m':500},'objectives':{},
      'unit_types_file':str((ROOT/'config/toe_templates.json').resolve()),
      'units':[{'id':'B','name':'B','side':'BLUE','echelon':'PLT','type':'INF_PLT','pos':[100,100],
                'element_overrides':[{'id':'rifle_1','weapon_ammo':{'AT4_MULTIROLE_GENERIC':3}}]}]
    }
    p=tmp_path/'s.json';p.write_text(json.dumps(raw))
    sim=load_scenario(p,bml_files={}); e=sim.units['B'].elements['rifle_1']
    w=next(w for w in e.weapons if w.metadata.get('weapon_id')=='AT4_MULTIROLE_GENERIC')
    assert w.ammo_remaining==3
    assert sim.units['B'].firepower(e,w).participants==3
