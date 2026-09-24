from mnsim.simulation import Simulation
from mnsim.model import Unit, UnitType, Side, FormationElement, WeaponModel


def _u(uid, side, pos, branch='INFANTRY'):
    ut=UnitType(name=branch,branch=branch,max_speed_mps=1.0,detection_range_m=900.0)
    el=FormationElement(eid=uid+'-e',name='team',role='RIFLE',category='PERSONNEL',count=4,initial_count=4,
        weapons=[WeaponModel(name='rifle',range_m=350,pk=.2,shots_per_min=5,capability='ANTI_PERSONNEL')])
    return Unit(uid=uid,name=uid,side=side,echelon='PLT',unit_type=ut,pos=pos,elements={el.eid:el})


def test_cross_platoon_track_report_traverses_comm_and_reorients_receiver():
    sim=Simulation(seed=11)
    a=_u('A',Side.BLUE,(0,0)); b=_u('B',Side.BLUE,(0,100)); e=_u('E',Side.RED,(500,0))
    b.watch_heading_deg=180.0
    sim.add_unit(a); sim.add_unit(b); sim.add_unit(e)
    sim.combat_config['communications']['default_link'].update({'min_delay_s':0.1,'max_delay_s':0.1,'reliability':1.0})
    payload={'target':'E','side':'BLUE','estimated_pos':(500,0),'position_error_m':40,'classification':'INFANTRY',
             'confidence':.8,'state':'CLASSIFIED','track_source':'SHARED','observation_time':sim.time}
    assert sim.communications.broadcast_side('A','TRACK_REPORT',payload)==1
    sim.tick(.11)
    assert 'E' in b.local_tracks
    assert b.local_tracks['E'].source=='SHARED'
    desired=sim._desired_watch_heading(b)
    # contact is southeast of B; shared cue should override routine facing without movement order
    assert abs(sim._angle_delta_deg(desired,-11.31)) < 5.0
    assert b.current_order is None


def test_jamming_can_block_delivery_without_changing_tactical_consumer():
    sim=Simulation(seed=12)
    a=_u('A',Side.BLUE,(0,0)); b=_u('B',Side.BLUE,(0,100)); e=_u('E',Side.RED,(500,0))
    sim.add_unit(a); sim.add_unit(b); sim.add_unit(e)
    sim.combat_config['communications']['global_jamming_strength']=1.0
    sim.combat_config['communications']['default_link'].update({'reliability':1.0,'jam_resistance':0.0})
    payload={'target':'E','estimated_pos':(500,0),'confidence':.8,'classification':'INFANTRY'}
    assert sim.communications.broadcast_side('A','TRACK_REPORT',payload)==0
    assert 'E' not in b.local_tracks


def test_engaged_shared_cue_has_hysteresis_and_does_not_create_order():
    sim=Simulation(seed=13)
    a=_u('A',Side.BLUE,(0,0)); b=_u('B',Side.BLUE,(0,100)); e=_u('E',Side.RED,(500,0))
    sim.add_unit(a); sim.add_unit(b); sim.add_unit(e)
    sim._register_shared_situational_cue(b,(500,0),.70,'A','CONTACT_ENGAGED')
    first=b.metadata['shared_cue_heading_deg']
    # Weaker ordinary contact from the opposite direction should not immediately whip the sector around.
    sim._register_shared_situational_cue(b,(-500,100),.50,'A','CONTACT')
    assert b.metadata['shared_cue_heading_deg']==first
    assert b.current_order is None


def test_observer_with_dead_radio_cannot_report_to_hq():
    from mnsim.scenario import load_scenario
    sim = load_scenario("scenarios/demo.json", bml_files={})
    obs = sim.units["B-INF-2"]
    obs.metadata["communications"] = {"tx_profile": {"reliability": 0.0, "max_range_m": 0.0}}
    assert not sim.communications.transmitter_operational(obs)
    assert sim.communications.transmitter_operational(sim.units["B-INF-3"])
