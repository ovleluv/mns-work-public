import json
from pathlib import Path
from mnsim.scenario import load_scenario
from mnsim.model import Track
from mnsim.bml import apply_bml_document

ROOT=Path(__file__).resolve().parents[1]

def _scenario(tmp_path):
    raw={
      'seed':11,'world':{'width_m':1000,'height_m':1000},'objectives':{},
      'unit_types_file':str((ROOT/'config/toe_templates.json').resolve()),
      'units':[
        {'id':'B-INF','name':'B-INF','side':'BLUE','echelon':'PLT','type':'INF_PLT','pos':[100,100],
         'element_overrides':[{'id':'rifle_1','weapon_ammo':{'AT4_MULTIROLE_GENERIC':2}}]},
        {'id':'R-INF','name':'R-INF','side':'RED','echelon':'SQD','type':'RIFLE_SQD','pos':[200,100]},
        {'id':'R-IFV','name':'R-IFV','side':'RED','echelon':'IND','type':'US_M2_BRADLEY_IND','pos':[220,100]},
        {'id':'B-MECH','name':'B-MECH','side':'BLUE','echelon':'PLT','type':'US_MECH_INF_PLT_BRADLEY','pos':[500,500]},
      ]}
    p=tmp_path/'s.json'; p.write_text(json.dumps(raw)); return load_scenario(p,bml_files={})

def _weapon(unit, weapon_id):
    for e,w in unit.operational_weapons():
        if w.metadata.get('weapon_id')==weapon_id:
            return e,w
    raise AssertionError(weapon_id)

def _track(target,state='IDENTIFIED',confidence=1.0):
    return Track(track_id='T',target_id=target.uid,estimated_pos=target.pos,position_error_m=2,
                 classification=target.branch,confidence=confidence,last_seen_time=0,observations=4,
                 source='LOCAL',observation_zone='FORWARD',state=state,belief_confidence=confidence,
                 existence_confirmed=True,last_confirmed_time=0)

def test_at4_is_editor_runtime_inventory_and_multirole(tmp_path):
    sim=_scenario(tmp_path); shooter=sim.units['B-INF']; inf=sim.units['R-INF']; ifv=sim.units['R-IFV']
    e,w=_weapon(shooter,'AT4_MULTIROLE_GENERIC')
    assert w.metadata['targeting_mode']=='UNGUIDED_LINE_OF_SIGHT'
    assert w.metadata['aim_point_capable'] is True and w.metadata['structure_capable'] is True
    assert w.metadata['inventory_model']=='DISPOSABLE_ROUNDS' and w.ammo_remaining==2
    assert shooter.firepower(e,w).participants==2
    assert 'system_count' not in w.metadata or int(w.metadata.get('system_count',0))==0
    assert sim.combat.weapon_can_affect(w,inf)
    assert sim.combat.weapon_can_affect(w,ifv)

def test_guided_at_requires_classified_lockable_armor_track(tmp_path):
    sim=_scenario(tmp_path); shooter=sim.units['B-INF']; inf=sim.units['R-INF']; ifv=sim.units['R-IFV']
    _,atgm=_weapon(shooter,'ATGM_GENERIC')
    detected=_track(ifv,state='DETECTED',confidence=.8)
    classified=_track(ifv,state='CLASSIFIED',confidence=.8)
    assert not sim.combat.weapon_can_affect(atgm,inf)
    assert not sim.combat.weapon_has_targeting_solution(atgm,detected,ifv)
    assert sim.combat.weapon_has_targeting_solution(atgm,classified,ifv)

def test_absolute_start_time_gates_new_dismount_order(tmp_path):
    sim=_scenario(tmp_path); mech=sim.units['B-MECH']
    apply_bml_document(sim,{'side':'BLUE','replace_existing_orders':True,'missions':[
        {'unit':'B-MECH','task':'DISMOUNT','start_at_s':30}
    ]})
    for _ in range(29*4): sim.tick(.25)
    assert mech.metadata['mount_state']=='MOUNTED'
    for _ in range(20*4): sim.tick(.25)
    assert mech.metadata['mount_state']=='DISMOUNTED'
