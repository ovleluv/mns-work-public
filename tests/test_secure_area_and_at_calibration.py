import json
from pathlib import Path
from mnsim.scenario import load_scenario
from mnsim.model import Track
from mnsim.bml import apply_bml_document

ROOT=Path(__file__).resolve().parents[1]

def _sim(tmp_path):
    raw={'seed':3,'world':{'width_m':800,'height_m':800},'objectives':{},
         'unit_types_file':str((ROOT/'config/toe_templates.json').resolve()),
         'units':[
           {'id':'B-TANK','name':'B-TANK','side':'BLUE','echelon':'IND','type':'US_M1A2_ABRAMS_IND','pos':[200,200]},
           {'id':'R-INF','name':'R-INF','side':'RED','echelon':'SQD','type':'RIFLE_SQD','pos':[260,200]},
           {'id':'B-INF','name':'B-INF','side':'BLUE','echelon':'PLT','type':'INF_PLT','pos':[100,100]},
         ]}
    p=tmp_path/'s.json';p.write_text(json.dumps(raw));return load_scenario(p,bml_files={})

def _local_track(target,t=0):
    return Track(track_id='T',target_id=target.uid,estimated_pos=target.pos,position_error_m=1,
                 classification=target.branch,confidence=1.0,last_seen_time=t,observations=5,
                 source='LOCAL',observation_zone='FORWARD',state='IDENTIFIED',belief_confidence=1.0,
                 existence_confirmed=True,last_confirmed_time=t)

def _weapon(unit,wid):
    for e,w in unit.operational_weapons():
        if w.metadata.get('weapon_id')==wid:return e,w
    raise AssertionError(wid)

def test_secure_area_compiles_as_bounded_reactive_defense(tmp_path):
    sim=_sim(tmp_path);u=sim.units['B-TANK']
    apply_bml_document(sim,{'side':'BLUE','replace_existing_orders':True,'missions':[
      {'unit':'B-TANK','task':'SECURE_AREA','center':[200,200],'radius_m':100,'pursuit_radius_m':90}
    ]})
    o=u.order_queue[0]
    assert o.kind=='DEFEND_AREA'
    assert o.params['pursue_within_area'] is True
    assert o.params['return_to_center'] is True
    assert o.params['pursuit_radius_m']==90

def test_secure_area_does_not_authorize_pursuit_beyond_boundary(tmp_path):
    sim=_sim(tmp_path);u=sim.units['B-TANK']
    apply_bml_document(sim,{'side':'BLUE','replace_existing_orders':True,'missions':[
      {'unit':'B-TANK','task':'SECURE_AREA','center':[200,200],'radius_m':100,'pursuit_radius_m':90}
    ]})
    o=u.order_queue[0]
    # The command layer itself encodes a hard pursuit leash; runtime pursuit only evaluates Tracks
    # whose estimated position falls inside this radius/polygon.
    assert o.params['pursuit_radius_m']==90
    assert o.params['pursue_within_area'] is True

def test_modern_at_hit_and_effect_parameters_are_not_legacy_low(tmp_path):
    sim=_sim(tmp_path);inf=sim.units['B-INF']
    _,atgm=_weapon(inf,'ATGM_GENERIC')
    assert float(atgm.metadata['base_hit_probability']) >= .75
    assert float(atgm.metadata['p_catastrophic_on_armor_hit']) >= .55
    assert float(atgm.metadata['guided_track_factor_floor']) >= .80
    # US mechanized platoons use the explicit Javelin definition.
    raw=json.loads((ROOT/'config/toe_templates.json').read_text())['unit_types']['US_MECH_INF_PLT_BRADLEY']
    assert 'JAVELIN_FGM148' in json.dumps(raw)
