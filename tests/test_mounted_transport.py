import json
from pathlib import Path
from mnsim.scenario import load_scenario
from mnsim.bml import apply_bml_document
from mnsim.model import Order
from mnsim.mounted import (transport_capacity, free_seats, required_vehicle_crew,
                           crew_available, dismount_organic, board_external, disembark_external)

ROOT=Path(__file__).resolve().parents[1]

def _scenario(tmp_path):
    raw={
      'seed':3,'world':{'width_m':1000,'height_m':1000},'objectives':{},
      'unit_types_file':str((ROOT/'config/toe_templates.json').resolve()),
      'units':[
        {'id':'B-MECH','name':'B-MECH','side':'BLUE','echelon':'PLT','type':'US_MECH_INF_PLT_BRADLEY','pos':[100,100],'orders':[]},
        {'id':'B-SQD','name':'B-SQD','side':'BLUE','echelon':'SQD','type':'RIFLE_SQD','pos':[110,100],'orders':[]},
        {'id':'B-BFV','name':'B-BFV','side':'BLUE','echelon':'IND','type':'US_M2_BRADLEY_IND','pos':[120,100],'orders':[]},
      ]}
    p=tmp_path/'s.json';p.write_text(json.dumps(raw));return load_scenario(p)

def _run(sim,seconds=60):
    for _ in range(int(seconds*4)):sim.tick(.25)

def test_bradley_default_capacity_and_crew(tmp_path):
    sim=_scenario(tmp_path);u=sim.units['B-MECH']
    assert transport_capacity(u)==24
    assert required_vehicle_crew(u)==12
    assert crew_available(u)==12
    # Organic dismounts exactly fit the four M2 passenger compartments.
    assert free_seats(sim,u)==0

def test_independent_bradley_has_minimum_crew_and_empty_seats(tmp_path):
    sim=_scenario(tmp_path);u=sim.units['B-BFV']
    assert required_vehicle_crew(u)==3
    assert crew_available(u)==3
    assert transport_capacity(u)==6
    assert free_seats(sim,u)==6

def test_organic_dismount_and_remount_conserve_personnel(tmp_path):
    sim=_scenario(tmp_path);u=sim.units['B-MECH']; before=u.personnel
    apply_bml_document(sim,{'side':'BLUE','replace_existing_orders':True,'missions':[{'unit':'B-MECH','task':'DISMOUNT'}]})
    _run(sim,20)
    child=sim.units[u.metadata['dismount_child_id']]
    assert child.active and child.personnel==24
    assert u.personnel==12 and u.metadata['mount_state']=='DISMOUNTED'
    apply_bml_document(sim,{'side':'BLUE','replace_existing_orders':True,'missions':[{'unit':'B-MECH','task':'MOUNT'}]})
    _run(sim,30)
    assert not child.active and u.personnel==before and u.metadata['mount_state']=='MOUNTED'


def test_mount_waits_for_seats_then_completes_after_passengers_leave(tmp_path):
    sim=_scenario(tmp_path); carrier=sim.units['B-MECH']; passenger=sim.units['B-SQD']
    child=dismount_organic(sim,carrier)
    assert child is not None and board_external(sim,passenger,carrier)
    carrier.current_order=Order('mount','MOUNT')
    carrier.metadata['embark_time_s']=0.0
    sim.tick(.25)
    assert carrier.current_order is not None
    assert child.active and carrier.metadata['mount_state']=='DISMOUNTED'
    assert not any(x['kind']=='ORDER_COMPLETE' and x['order_id']=='mount' for x in sim.logs)

    disembark_external(sim,carrier,passenger.uid)
    sim.tick(.25)
    assert carrier.current_order is None
    assert not child.active and carrier.metadata['mount_state']=='MOUNTED'

def test_external_squad_boards_empty_bradley_and_returns(tmp_path):
    sim=_scenario(tmp_path);sqd=sim.units['B-SQD']; carrier=sim.units['B-BFV']
    # RIFLE_SQD may be larger than a single M2 compartment; override to a six-person test squad.
    for e in sqd.elements.values():
        if e.category.upper()=='PERSONNEL': e.count=0;e.initial_count=0
    first=next(e for e in sqd.elements.values() if e.category.upper()=='PERSONNEL')
    first.count=6;first.initial_count=6
    apply_bml_document(sim,{'side':'BLUE','replace_existing_orders':True,'missions':[{'unit':'B-SQD','task':'BOARD','carrier':'B-BFV'}]})
    _run(sim,140)
    assert not sqd.active and sqd.metadata['embarked_in']=='B-BFV'
    assert free_seats(sim,carrier)==0
    alloc=carrier.metadata['external_passenger_allocations']['B-SQD']
    assert sum(x['seats'] for x in alloc)==6 and alloc[0]['vehicle_index']==0
    apply_bml_document(sim,{'side':'BLUE','replace_existing_orders':False,'missions':[{'unit':'B-BFV','task':'DISEMBARK','passengers':'ALL'}]})
    _run(sim,20)
    assert sqd.active and 'embarked_in' not in sqd.metadata and free_seats(sim,carrier)==6


def test_board_completion_resets_order_clock_before_disembark(tmp_path):
    sim=_scenario(tmp_path); passenger=sim.units['B-SQD']; carrier=sim.units['B-BFV']
    for element in passenger.elements.values():
        if element.category.upper()=='PERSONNEL':
            element.count=element.initial_count=0
    first=next(e for e in passenger.elements.values() if e.category.upper()=='PERSONNEL')
    first.count=first.initial_count=6
    passenger.order_queue=[Order('board','BOARD',{'carrier':carrier.uid}),
                           Order('wait','WAIT',{'duration_s':5})]
    _run(sim,25)
    assert passenger.metadata.get('embarked_in')==carrier.uid
    assert any(x['kind']=='ORDER_COMPLETE' and x['order_id']=='board' for x in sim.logs)
    assert 'order_started_t' not in passenger.metadata

    disembark_external(sim,carrier,passenger.uid)
    sim.tick(.25)
    assert passenger.current_order is not None and passenger.current_order.order_id=='wait'
    assert not any(x['kind']=='ORDER_COMPLETE' and x['order_id']=='wait' for x in sim.logs)

def test_editor_override_metadata_reaches_runtime(tmp_path):
    raw={
      'seed':3,'world':{'width_m':1000,'height_m':1000},'objectives':{},
      'unit_types_file':str((ROOT/'config/toe_templates.json').resolve()),
      'units':[{'id':'B','name':'B','side':'BLUE','echelon':'IND','type':'US_M2_BRADLEY_IND','pos':[0,0],
                'element_overrides':[{'id':'vehicle','count':2,'initial_count':2,'metadata':{'crew':2,'passengers':8}},
                                     {'id':'vehicle_crew','count':4,'initial_count':4}], 'orders':[]}]
    }
    p=tmp_path/'o.json';p.write_text(json.dumps(raw));sim=load_scenario(p);u=sim.units['B']
    assert transport_capacity(u)==16 and required_vehicle_crew(u)==4 and crew_available(u)==4
