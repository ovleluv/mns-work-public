from mnsim.scenario import load_scenario
from mnsim.model import Track


def _track(observer,target,now=0.0):
    observer.local_tracks[target.uid]=Track(
        track_id=f'{observer.uid}:{target.uid}',target_id=target.uid,estimated_pos=target.pos,
        position_error_m=2.0,classification=target.branch,confidence=.98,last_seen_time=now,
        observations=6,source='LOCAL',state='IDENTIFIED',belief_confidence=.98,
        existence_confirmed=True,last_confirmed_time=now,observation_zone='FORWARD')


def test_abrams_has_coax_hmg_and_loader_mg():
    sim=load_scenario('scenarios/demo.json')
    tank=sim.units['B-TK-1'] if 'B-TK-1' in sim.units else next(u for u in sim.units.values() if u.branch=='ARMOR')
    names=[w.name for _,w in tank.operational_weapons()]
    # Generic tank formations must at minimum expose coax; explicit M1A2 template is checked from catalog below.
    assert any(w.metadata.get('ui_range_label')=='COAX MG' for _,w in tank.operational_weapons())
    import json
    defs=json.load(open('config/toe_templates.json'))['unit_types']['US_M1A2_ABRAMS_IND']
    ids=[w['weapon_id'] for e in defs['elements'] for w in e.get('weapons',[])]
    assert 'COAX_MG_GENERIC' in ids and 'HMG_GENERIC' in ids and 'GPMG_SINGLE_GENERIC' in ids


def test_machine_gun_model_is_lethality_not_suppression():
    sim=load_scenario('scenarios/demo.json')
    shooter=sim.units['B-TK-1']; target=sim.units['R-INF-1']
    el,w=next((e,w) for e,w in shooter.operational_weapons() if w.metadata.get('ui_range_label')=='COAX MG')
    assert not w.metadata.get('suppression_capable',False)
    assert 'suppression_per_cycle' not in w.metadata
    assert float(w.pk) >= 0.25
    assert int(w.max_effect_count) >= 2

def test_direct_fire_cycle_and_reload_are_explicit():
    sim=load_scenario('scenarios/demo.json')
    shooter=sim.units['B-TK-1']; target=sim.units['R-INF-1']
    shooter.pos=(1000.,1000.); target.pos=(1150.,1000.);sim.time=10.;_track(shooter,target,sim.time); shooter.target_id=target.uid; shooter.metadata['target_acquired_t']=sim.time
    el,w=next((e,w) for e,w in shooter.operational_weapons() if w.metadata.get('ui_range_label')=='COAX MG')
    assert w.metadata['engagement_cycle_min_s']>=4.0
    assert w.metadata['reload_after_cycles']>=6
    sim.combat.fire_weapon(shooter,target,el,w)
    st=shooter.metadata['_direct_fire_state'][f'{el.eid}:{w.name}'];sim.time=float(st['ready_at'])+.01
    sim.combat.fire_weapon(shooter,target,el,w)
    cst=shooter.metadata['_direct_fire_cycle_state'][f'{el.eid}:{w.name}']
    assert cst['next_ready_at']>sim.time
