import json
from pathlib import Path
import pytest
from mnsim.scenario import load_scenario
from mnsim.model import Track, UnitState
from mnsim.bml import apply_bml_document


def _write_minimal(tmp_path):
    toe={"unit_types":{"INF_PLT":{"branch":"INFANTRY","max_speed_mps":2.0,"detection_range_m":500.0,"metadata":{"echelon":"PLT","formation_family":"INF"},"elements":[{"id":"rifle","category":"PERSONNEL","role":"RIFLE","count":10,"weapons":[{"name":"rifle","range_m":300,"pk":0.1}]}]}}}
    (tmp_path/'toe.json').write_text(json.dumps(toe))
    (tmp_path/'terrain.json').write_text(json.dumps({}))
    sc={"seed":1,"world":{"width_m":1000,"height_m":1000},"objectives":{},"terrain_file":"terrain.json","unit_types_file":"toe.json","bml_files":{"BLUE":"blue.json","RED":"red.json"},"units":[
      {"id":"B1","side":"BLUE","echelon":"PLT","type":"INF_PLT","pos":[100,100],"orders":[{"id":"old","kind":"HOLD","params":{"duration_s":9999}}]},
      {"id":"B2","side":"BLUE","echelon":"PLT","type":"INF_PLT","pos":[150,100],"orders":[{"id":"keep","kind":"HOLD","params":{"duration_s":9999}}]},
      {"id":"R1","side":"RED","echelon":"PLT","type":"INF_PLT","pos":[800,800],"orders":[{"id":"oldr","kind":"HOLD","params":{"duration_s":9999}}]}
    ]}
    (tmp_path/'scenario.json').write_text(json.dumps(sc))
    return tmp_path/'scenario.json'


def test_external_bml_replaces_only_commanded_units(tmp_path):
    sp=_write_minimal(tmp_path)
    (tmp_path/'blue.json').write_text(json.dumps({"side":"BLUE","missions":[{"unit":"B1","task":"DESTROY_UNIT","target":"R1"}]}))
    (tmp_path/'red.json').write_text(json.dumps({"side":"RED","missions":[{"unit":"R1","task":"DEFEND_AREA","center":[800,800],"radius_m":80}]}))
    sim=load_scenario(str(sp))
    assert [o.kind for o in sim.units['B1'].order_queue]==['DESTROY_UNIT']
    assert [o.kind for o in sim.units['B2'].order_queue]==['HOLD']
    assert [o.kind for o in sim.units['R1'].order_queue]==['DEFEND_AREA']
    assert sim.units['B1'].order_queue[0].params['target_unit']=='R1'


def test_entity_attack_without_track_or_explicit_reference_does_not_move(tmp_path):
    sp=_write_minimal(tmp_path)
    (tmp_path/'blue.json').write_text(json.dumps({"side":"BLUE","missions":[{"unit":"B1","task":"ATTACK_UNIT","target":"R1"}]}))
    (tmp_path/'red.json').write_text(json.dumps({"side":"RED","missions":[]}))
    sim=load_scenario(str(sp))
    order=sim.units['B1'].order_queue[0]
    assert 'initial_target_pos' not in order.params
    assert 'search_reference' not in order.params

    before=sim.units['B1'].pos
    sim.tick(1.0)
    assert sim.units['B1'].pos == before
    assert sim.units['B1'].state == UnitState.SEARCHING
    assert 'TARGET LOCATION UNKNOWN' in sim.units['B1'].metadata.get('tactical_reason','')


def test_entity_attack_uses_only_explicit_bml_target_position_when_untracked(tmp_path):
    sp=_write_minimal(tmp_path)
    (tmp_path/'blue.json').write_text(json.dumps({"side":"BLUE","missions":[
        {"unit":"B1","task":"ATTACK_UNIT","target":"R1","target_position":[100,800]}
    ]}))
    (tmp_path/'red.json').write_text(json.dumps({"side":"RED","missions":[]}))
    sim=load_scenario(str(sp))
    # Ground-truth target is elsewhere. The unit must follow only the explicitly supplied BML reference.
    sim.units['R1'].pos=(800,100)
    sim.tick(1.0)
    x,y=sim.units['B1'].pos
    assert abs(x-100.0) < 0.2, (x,y)
    assert y>100.0, (x,y)
    assert sim.units['B1'].order_queue == []


def test_four_sequential_entity_missions_use_each_orders_own_reference(tmp_path):
    sp=_write_minimal(tmp_path)
    raw=json.loads(Path(sp).read_text())
    raw['units'].extend([
        {"id":"R2","side":"RED","echelon":"PLT","type":"INF_PLT","pos":[100,800],"orders":[]},
        {"id":"R3","side":"RED","echelon":"PLT","type":"INF_PLT","pos":[200,100],"orders":[]},
        {"id":"R4","side":"RED","echelon":"PLT","type":"INF_PLT","pos":[100,200],"orders":[]},
    ])
    Path(sp).write_text(json.dumps(raw))
    refs={"R1":[800,800],"R2":[100,800],"R3":[200,100],"R4":[100,200]}
    (tmp_path/'blue.json').write_text(json.dumps({"side":"BLUE","missions":[
        {"unit":"B1","task":"DESTROY_UNIT","target":tid,"target_position":refs[tid]}
        for tid in ("R1","R2","R3","R4")
    ]}))
    (tmp_path/'red.json').write_text(json.dumps({"side":"RED","missions":[]}))
    sim=load_scenario(str(sp))
    b=sim.units['B1']

    # Walk through all four mission transitions. Stale contact/navigation metadata from the previous
    # target must never select the next target's destination.
    expected=("R1","R2","R3","R4")
    for i,tid in enumerate(expected):
        sim.tick(0.1)
        assert b.current_order is not None
        assert b.current_order.params['target_unit']==tid
        assert tuple(b.current_order.params['search_reference'])==tuple(refs[tid])
        # Poison formation-global contact memory to emulate a previous/lost contact. It must not drive
        # an unrelated subsequent entity mission.
        b.metadata['last_contact_id']='STALE-'+tid
        b.metadata['last_contact_pos']=(999.0,999.0)
        sim.units[tid].state=UnitState.DESTROYED
        # A real loss is not automatically known to the commander.
        sim.tick(0.1)
        assert b.current_order.params['target_unit']==tid
        observed=Track(f"B1:{tid}",tid,tuple(refs[tid]),5.0,
                       classification="INFANTRY",confidence=.9,last_seen_time=sim.time,
                       source="LOCAL",state="IDENTIFIED",existence_confirmed=True)
        b.local_tracks[tid]=observed
        sim.belief.mark_destroyed(observed)
        sim.tick(0.1)

    sim.tick(0.1)
    assert b.current_order is None
    assert b.order_queue == []


def test_attack_position_is_coordinate_based_and_requires_no_enemy_identity(tmp_path):
    sp=_write_minimal(tmp_path)
    (tmp_path/'blue.json').write_text(json.dumps({"side":"BLUE","missions":[
        {"unit":"B1","task":"ATTACK_POSITION","destination":[100,700]}
    ]}))
    (tmp_path/'red.json').write_text(json.dumps({"side":"RED","missions":[]}))
    sim=load_scenario(str(sp))
    order=sim.units['B1'].order_queue[0]
    assert order.kind == 'ATTACK'
    assert order.params['destination'] == [100,700]
    assert 'target_unit' not in order.params
    sim.tick(1.0)
    x,y=sim.units['B1'].pos
    assert abs(x-100.0) < 0.2 and y>100.0, (x,y)


def test_replace_clears_active_order_once_and_retains_all_new_missions(tmp_path):
    sim=load_scenario(str(_write_minimal(tmp_path)),bml_files={})
    b=sim.units['B1']
    sim._step_unit(b,0.0)
    assert b.current_order.order_id=='old'
    apply_bml_document(sim,{'side':'BLUE','missions':[
        {'id':'first','unit':'B1','task':'MOVE_TO','destination':[200,100]},
        {'id':'second','unit':'B1','task':'HOLD'}]},expected_side='BLUE')
    assert b.current_order is None
    assert [o.order_id for o in b.order_queue]==['first','second']
    assert sim.units['B2'].order_queue[0].order_id=='keep'
    before=b.pos
    sim._step_unit(b,1.0)
    assert b.current_order.order_id=='first' and b.pos[0]>before[0]


def test_append_does_not_preempt_an_indefinite_active_hold(tmp_path):
    sim=load_scenario(str(_write_minimal(tmp_path)),bml_files={})
    b=sim.units['B1']
    b.order_queue[0].params['duration_s']=-1
    sim._step_unit(b,0.0)
    active=b.current_order
    apply_bml_document(sim,{'side':'BLUE','replace_existing_orders':False,'missions':[
        {'id':'later','unit':'B1','task':'MOVE_TO','destination':[200,100]}]},expected_side='BLUE')
    before=b.pos
    sim.time=10000
    sim._step_unit(b,1.0)
    assert b.current_order is active and b.pos==before
    assert [o.order_id for o in b.order_queue]==['later']


def test_append_executes_only_after_existing_finite_order_completes(tmp_path):
    sim=load_scenario(str(_write_minimal(tmp_path)),bml_files={})
    b=sim.units['B1']
    b.order_queue[0].params['duration_s']=2
    apply_bml_document(sim,{'side':'BLUE','replace_existing_orders':False,'missions':[
        {'id':'later','unit':'B1','task':'MOVE_TO','destination':[200,100]}]},expected_side='BLUE')
    sim._step_unit(b,0.0)
    before=b.pos
    sim.time=1
    sim._step_unit(b,1.0)
    assert b.pos==before and b.current_order.order_id=='old'
    sim.time=2
    sim._step_unit(b,1.0)
    assert b.current_order is None and b.pos==before
    sim.time=2.25
    sim._step_unit(b,.25)
    assert b.current_order.order_id=='later' and b.pos[0]>before[0]


def test_wrong_side_mission_is_rejected_before_replacing_enemy_orders(tmp_path):
    sim=load_scenario(str(_write_minimal(tmp_path)),bml_files={})
    old=list(sim.units['R1'].order_queue)
    with pytest.raises(ValueError,match='attempts to command'):
        apply_bml_document(sim,{'side':'BLUE','missions':[
            {'unit':'R1','task':'MOVE_TO','destination':[200,100]}]},expected_side='BLUE')
    assert sim.units['R1'].order_queue==old


def test_phases_advance_per_unit_without_an_implicit_team_barrier(tmp_path):
    sim=load_scenario(str(_write_minimal(tmp_path)),bml_files={})
    apply_bml_document(sim,{'side':'BLUE','phases':[
        {'id':'ASSEMBLY','missions':[
            {'unit':'B1','task':'HOLD','duration_s':1},
            {'unit':'B2','task':'HOLD','duration_s':10}]},
        {'id':'ADVANCE','missions':[
            {'unit':'B1','task':'MOVE_TO','destination':[200,100]},
            {'unit':'B2','task':'MOVE_TO','destination':[250,100]}]}
    ]},expected_side='BLUE')
    first,second=sim.units['B1'],sim.units['B2']
    for u in (first,second):sim._step_unit(u,0)
    first_pos,second_pos=first.pos,second.pos
    sim.time=1
    for u in (first,second):sim._step_unit(u,1)
    sim.time=1.25
    for u in (first,second):sim._step_unit(u,.25)
    assert first.current_order.phase_id=='ADVANCE' and first.pos[0]>first_pos[0]
    assert second.current_order.phase_id=='ASSEMBLY' and second.pos==second_pos
