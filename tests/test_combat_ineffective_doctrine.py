import math
from mnsim.scenario import load_scenario
from mnsim.model import Track, UnitState


def _firepower_kill_all_tanks(tank):
    el=tank.elements['tanks']
    el.ensure_item_states()
    el.item_states=['FIREPOWER_KILL']*len(el.item_states)
    el.sync_count_from_states()
    assert el.mobile_item_count == el.count
    assert el.fire_capable_item_count == 0
    assert tank.operational_weapons() == []


def test_firepower_killed_tank_does_not_continue_attack_without_contact():
    sim=load_scenario('scenarios/demo.json')
    tank=sim.units['B-TK-1']
    _firepower_kill_all_tanks(tank)
    tank.local_tracks.clear()
    start=tuple(tank.pos)
    assert tank.current_order is not None or tank.order_queue
    sim._step_unit(tank, 1.0)
    assert tuple(tank.pos) == start
    assert tank.state == UnitState.DEFENDING
    assert 'NO OPERATIONAL WEAPON CAPABILITY' in tank.metadata.get('tactical_reason','')


def test_firepower_killed_tank_makes_one_bounded_withdrawal_not_ping_pong():
    sim=load_scenario('scenarios/demo.json')
    tank=sim.units['B-TK-1']
    enemy=sim.units['R-TK-1']
    _firepower_kill_all_tanks(tank)

    # Put a stable perceived threat east of the tank. Doctrine must withdraw west once and hold.
    enemy.pos=(tank.pos[0]+200.0, tank.pos[1])
    tank.local_tracks={enemy.uid: Track(
        track_id=f'{tank.uid}:{enemy.uid}', target_id=enemy.uid,
        estimated_pos=enemy.pos, position_error_m=5.0,
        classification='ARMOR', confidence=1.0, last_seen_time=sim.time,
        observations=3, source='LOCAL', state='IDENTIFIED',
        belief_confidence=1.0, existence_confirmed=True, last_confirmed_time=sim.time,
    )}
    start=tuple(tank.pos)
    xs=[]
    for _ in range(250):
        sim.doctrine.step(tank, 0.25)
        xs.append(tank.pos[0])
    # Threat is east, so every movement step must be westward or stationary; no objective/contact oscillation.
    assert all(b <= a + 1e-9 for a,b in zip([start[0]]+xs[:-1], xs))
    assert math.dist(start,tank.pos) <= sim.combat_config['combat_ineffective_withdraw_m'] + 5.0
    held=tuple(tank.pos)
    for _ in range(20):
        sim.doctrine.step(tank,0.25)
    assert math.dist(held,tank.pos) < 1e-6
    assert tank.state == UnitState.DEFENDING
