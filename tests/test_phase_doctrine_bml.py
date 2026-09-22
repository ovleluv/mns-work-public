import json
from pathlib import Path
from mnsim.scenario import load_scenario
from mnsim.model import Track, UnitState


def _base_files(tmp_path, *, doctrine=False):
    toe={"unit_types":{
        "INF_PLT":{"branch":"INFANTRY","max_speed_mps":2.0,"detection_range_m":500.0,
                   "metadata":{"echelon":"PLT","formation_family":"INF"},
                   "elements":[{"id":"rifle","category":"PERSONNEL","role":"RIFLE","count":10,
                                "weapons":[{"name":"rifle","range_m":300,"pk":0.1}]}]},
        "TANK_PLT":{"branch":"ARMOR","max_speed_mps":4.0,"detection_range_m":600.0,
                    "metadata":{"echelon":"PLT","formation_family":"TANK"},
                    "elements":[{"id":"tank","category":"EQUIPMENT","role":"TANK","count":1,
                                 "weapons":[{"name":"gun","range_m":1000,"pk":0.2,"capability":"ANTI_ARMOR"}]}]}
    }}
    (tmp_path/'toe.json').write_text(json.dumps(toe))
    (tmp_path/'terrain.json').write_text('{}')
    if doctrine:
        (tmp_path/'doctrine.json').write_text(json.dumps({"profiles":{
            "GENERIC_DEFAULT":{},
            "STAND_FAST":{"infantry_break_contact_if_no_at":False}
        }}))
    return toe


def _scenario(tmp_path, *, bml=None, doctrine=False, profile=None):
    _base_files(tmp_path, doctrine=doctrine)
    units=[
        {"id":"B1","side":"BLUE","echelon":"PLT","type":"INF_PLT","pos":[100,100],
         **({"doctrine_profile":profile} if profile else {})},
        {"id":"R1","side":"RED","echelon":"PLT","type":"TANK_PLT","pos":[800,800]}
    ]
    sc={"seed":1,"world":{"width_m":1000,"height_m":1000},"terrain_file":"terrain.json",
        "unit_types_file":"toe.json","units":units}
    if doctrine: sc["doctrine_profiles_file"]="doctrine.json"
    if bml:
        (tmp_path/'blue.json').write_text(json.dumps(bml))
        sc["bml_files"]={"BLUE":"blue.json"}
    (tmp_path/'scenario.json').write_text(json.dumps(sc))
    return load_scenario(str(tmp_path/'scenario.json'))


def test_phase_start_time_gates_movement_and_phase_metadata(tmp_path):
    bml={"side":"BLUE","phases":[
        {"id":"PHASE_ALPHA","start_at_s":5.0,"missions":[
            {"unit":"B1","task":"MOVE_TO","destination":[200,100]}
        ]}
    ]}
    sim=_scenario(tmp_path,bml=bml)
    order=sim.units['B1'].order_queue[0]
    assert order.phase_id=='PHASE_ALPHA' and order.start_at_s==5.0
    start=sim.units['B1'].pos
    sim.tick(4.0)
    assert sim.units['B1'].pos==start
    sim.tick(1.0)
    assert sim.units['B1'].pos[0] > start[0]


def test_time_condition_branches_without_damage_event(tmp_path):
    bml={"side":"BLUE","missions":[{
        "id":"WAIT-THEN-GO","unit":"B1","task":"HOLD",
        "conditions":[{"lhs":"sim.time","op":">=","rhs":3.0}],
        "on_true":{"id":"GO","task":"MOVE_TO","destination":[200,100]}
    }]}
    sim=_scenario(tmp_path,bml=bml)
    sim.tick(2.0)
    assert sim.units['B1'].current_order.kind=='HOLD'
    sim.tick(1.0)
    assert sim.units['B1'].current_order.kind=='MOVE'
    x=sim.units['B1'].pos[0]
    sim.tick(1.0)
    assert sim.units['B1'].pos[0] > x


def test_deadline_can_branch_without_speed_cheat(tmp_path):
    bml={"side":"BLUE","missions":[{
        "id":"RALLY","unit":"B1","task":"MOVE_TO","destination":[900,100],
        "deadline_s":2.0,
        "on_deadline":{"id":"MISSED-RALLY","task":"HOLD","duration_s":999}
    }]}
    sim=_scenario(tmp_path,bml=bml)
    sim.tick(1.0)
    x=sim.units['B1'].pos[0]
    assert 100 < x < 900
    sim.tick(1.0)
    assert sim.units['B1'].current_order.kind=='HOLD'
    assert any(r['kind']=='ORDER_DEADLINE_MISSED' for r in sim.logs)
    assert any(r['kind']=='DEADLINE_BRANCH' for r in sim.logs)


def _armor_contact(sim):
    inf=sim.units['B1']; armor=sim.units['R1']
    armor.pos=(inf.pos[0]+100,inf.pos[1])
    inf.local_tracks={armor.uid:Track(
        track_id='B1:R1',target_id='R1',estimated_pos=armor.pos,position_error_m=3,
        classification='ARMOR',confidence=1.0,last_seen_time=sim.time,observations=3,
        source='LOCAL',state='IDENTIFIED',belief_confidence=1.0,existence_confirmed=True,
        last_confirmed_time=sim.time)}
    return inf,armor


def test_same_branch_can_use_different_doctrine_profile(tmp_path):
    sim=_scenario(tmp_path,doctrine=True,profile='STAND_FAST')
    inf,_=_armor_contact(sim)
    start=inf.pos
    handled=sim.doctrine.step(inf,1.0)
    assert handled is False
    assert inf.pos==start


def test_mission_hold_at_all_costs_overrides_generic_break_contact(tmp_path):
    bml={"side":"BLUE","missions":[{
        "unit":"B1","task":"HOLD",
        "directives":{"hold_at_all_costs":True,"allow_withdrawal":False}
    }]}
    sim=_scenario(tmp_path,bml=bml)
    inf,_=_armor_contact(sim)
    # Activate queued BML order without running sensing/combat.
    sim._step_unit(inf,0.0)
    start=inf.pos
    handled=sim.doctrine.step(inf,1.0)
    assert handled is True
    assert inf.state==UnitState.DEFENDING
    assert inf.pos==start
    assert 'WITHDRAWAL PROHIBITED' in inf.metadata.get('tactical_reason','')


def test_loss_condition_can_redirect_to_rally_point(tmp_path):
    bml={"side":"BLUE","missions":[{
        "id":"DEFEND-UNTIL-ATTRITED","unit":"B1","task":"HOLD",
        "conditions":[{"lhs":"self.loss_ratio","op":">=","rhs":0.5}],
        "on_true":{"id":"RALLY-ALPHA","task":"WITHDRAW","destination":[50,50]}
    }]}
    sim=_scenario(tmp_path,bml=bml)
    inf=sim.units['B1']
    sim._step_unit(inf,0.0)
    inf.elements['rifle'].count=5
    sim._step_unit(inf,0.1)
    assert inf.current_order.kind=='RETREAT'
    assert inf.current_order.params['destination']==[50,50]
