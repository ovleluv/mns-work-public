from mnsim.scenario import load_scenario
from mnsim.model import Order, Track


def _prepare_bn_vs_platoons():
    sim=load_scenario('scenarios/tdg1.json')
    shooter=sim.units['B-INF_BN-1']
    enemies=[sim.units[x] for x in ('R-INF_PLT-1','R-INF_PLT-2','R-INF_PLT-5','R-INF_PLT-6')]
    shooter.pos=(2000.0,2000.0)
    positions=[(2240.0,2000.0),(2000.0,2240.0),(1760.0,2000.0),(2000.0,1760.0)]
    shooter.local_tracks.clear()
    shooter.metadata.pop('_local_direct_target_locks',None)
    shooter.metadata.pop('_direct_fire_state',None)
    shooter.target_id=None
    shooter.current_order=None
    for e,pos in zip(enemies,positions):
        e.pos=pos
        shooter.local_tracks[e.uid]=Track(
            track_id=f'{shooter.uid}:{e.uid}',target_id=e.uid,estimated_pos=pos,
            position_error_m=1.0,classification='INFANTRY',confidence=.95,
            last_seen_time=sim.time,observations=5,source='LOCAL',state='IDENTIFIED',
            belief_confidence=.95,existence_confirmed=True,last_confirmed_time=sim.time,
        )
    return sim,shooter,enemies


def test_bn_direct_fire_is_distributed_across_multiple_actionable_enemy_formations():
    sim,shooter,enemies=_prepare_bn_vs_platoons()
    targets=sim.combat.fire_local(shooter,enemies,{})
    # The battalion has many squad/weapon streams.  With four equally actionable platoons around
    # it, those streams must not all inherit a single formation-level target.
    assert len(set(targets)) >= 3, set(targets)


def test_entity_mission_target_is_priority_not_exclusive_fire_target():
    sim,shooter,enemies=_prepare_bn_vs_platoons()
    mission_target=enemies[0].uid
    shooter.current_order=Order('T','DESTROY_UNIT',{'target_unit':mission_target})
    targets=sim.combat.fire_local(shooter,enemies,{})
    assert mission_target in targets
    assert len(set(targets)) >= 2, 'mission target must not monopolize all subordinate fire streams'


def test_local_target_lock_is_scoped_to_element_weapon_not_whole_formation():
    sim,shooter,enemies=_prepare_bn_vs_platoons()
    sim.combat.fire_local(shooter,enemies,{})
    locks=shooter.metadata.get('_local_direct_target_locks',{})
    assert len(locks) > 4
    assert len({v['target_id'] for v in locks.values()}) >= 3
