from mnsim.scenario import load_scenario
from mnsim.model import Track, UnitState


def _setup(zones, positions):
    sim = load_scenario('scenarios/tdg1.json')
    shooter = sim.units['B-INF_BN-1']
    enemy_ids = ['R-INF_PLT-1','R-INF_PLT-2','R-INF_PLT-5','R-INF_PLT-6'][:len(zones)]
    enemies = [sim.units[x] for x in enemy_ids]
    shooter.pos=(2000.0,2000.0)
    shooter.local_tracks.clear()
    shooter.metadata.pop('_local_direct_target_locks',None)
    shooter.metadata.pop('_direct_fire_state',None)
    shooter.metadata.pop('objective',None)
    shooter.metadata.pop('threat_cue_until_t',None)
    shooter.current_order=None
    shooter.state=UnitState.IDLE
    shooter.target_id=None
    shooter.watch_heading_deg=0.0
    for enemy,zone,pos in zip(enemies,zones,positions):
        enemy.pos=pos
        shooter.local_tracks[enemy.uid]=Track(
            track_id=f'{shooter.uid}:{enemy.uid}', target_id=enemy.uid,
            estimated_pos=pos, position_error_m=1.0, classification='INFANTRY',
            confidence=.95, last_seen_time=sim.time, observations=5, source='LOCAL',
            observation_zone=zone, state='IDENTIFIED', belief_confidence=.95,
            existence_confirmed=True, last_confirmed_time=sim.time,
        )
    return sim, shooter, enemies


def _capture_fire(sim):
    fired=[]
    original=sim.combat.fire_weapon
    def capture(shooter,target,source_element,weapon,mode='DIRECT'):
        fired.append(target.uid)
    sim.combat.fire_weapon=capture
    return fired, original


def test_close_all_round_contacts_receive_multi_target_local_allocation():
    positions=[(2120,2000),(2000,2120),(1880,2000),(2000,1880)]
    sim,shooter,enemies=_setup(['CLOSE']*4,positions)
    primary=sim.combat.select_target(shooter,enemies,'DIRECT')
    fired,_=_capture_fire(sim)
    sim.combat.fire_hybrid(shooter,enemies,primary,{})
    assert len(set(fired)) >= 3, set(fired)


def test_forward_sector_contacts_preserve_single_formation_primary_target():
    positions=[(2240,2000),(2230,2040),(2220,1960)]
    sim,shooter,enemies=_setup(['FORWARD']*3,positions)
    primary=sim.combat.select_target(shooter,enemies,'DIRECT')
    fired,_=_capture_fire(sim)
    result=sim.combat.fire_hybrid(shooter,enemies,primary,{})
    assert result == [primary.uid]
    assert fired and set(fired)=={primary.uid}


def test_close_contact_does_not_reorient_principal_watch_sector():
    sim,shooter,enemies=_setup(['CLOSE'],[(2000,2120)])
    sim.combat_config['watch_sweep']={'enabled':False}   # isolate the target-cue rule
    shooter.target_id=enemies[0].uid
    assert sim._desired_watch_heading(shooter) == 0.0


def test_forward_contact_can_reorient_principal_watch_sector():
    sim,shooter,enemies=_setup(['FORWARD'],[(2000,2240)])
    shooter.target_id=enemies[0].uid
    desired=sim._desired_watch_heading(shooter)
    assert 89.0 <= desired <= 91.0
