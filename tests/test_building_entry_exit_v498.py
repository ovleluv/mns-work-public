import json
from pathlib import Path
from mnsim.scenario import load_scenario
from mnsim.bml import compile_mission

ROOT=Path(__file__).resolve().parents[1]

def _scenario(tmp_path):
    terrain={"areas":[{"id":"BLD1","type":"BUILDING","polygon":[[220,160],[320,160],[320,260],[220,260]],"height_m":8}]}
    (tmp_path/'terrain.json').write_text(json.dumps(terrain))
    src=json.loads((ROOT/'scenarios/test2.json').read_text())
    src['terrain_file']='terrain.json'
    src['config_file']=str((ROOT/'config/defaults.json').resolve())
    src['unit_types_file']=str((ROOT/'config/toe_templates.json').resolve())
    src['artillery_doctrine_file']=str((ROOT/'config/artillery_doctrine.json').resolve())
    src['targeting_doctrine_file']=str((ROOT/'config/targeting_doctrine.json').resolve())
    src['units']=[src['units'][1]]
    src['units'][0]['pos']=[120,210]
    src['units'][0]['orders']=[]
    sp=tmp_path/'s.json'; sp.write_text(json.dumps(src)); return load_scenario(str(sp))

def test_move_route_avoids_building(tmp_path):
    sim=_scenario(tmp_path); u=sim.units['B-INF_PLT-1']
    route=sim.terrain.plan_route(u,(420,210))
    assert len(route)>2
    assert all(sim.terrain.building_at(tuple(p)) is None for p in route[1:-1])

def test_enter_and_exit_building_are_explicit_bml_tasks(tmp_path):
    sim=_scenario(tmp_path); u=sim.units['B-INF_PLT-1']
    _,enter=compile_mission(sim,{"unit":u.uid,"task":"ENTER_BUILDING","target_structure":"BLD1"})
    assert enter.kind=='ENTER_BUILDING'
    sim.issue_order(u.uid,enter)
    for _ in range(1200):
        sim.tick(0.5)
        if u.current_order is None and not u.order_queue:break
    assert sim.terrain.building_at(u.pos) is sim.terrain.building_by_id('BLD1')
    assert u.metadata.get('occupied_building_id')=='BLD1'
    assert '_building_access_id' not in u.metadata

    _,exit_order=compile_mission(sim,{"unit":u.uid,"task":"EXIT_BUILDING","target_structure":"BLD1","destination":[380,210]})
    sim.issue_order(u.uid,exit_order)
    for _ in range(1200):
        sim.tick(0.5)
        if u.current_order is None and not u.order_queue:break
    assert sim.terrain.building_at(u.pos) is None
    assert 'occupied_building_id' not in u.metadata

def test_plain_move_cannot_cross_building_footprint(tmp_path):
    sim=_scenario(tmp_path); u=sim.units['B-INF_PLT-1']
    _,move=compile_mission(sim,{"unit":u.uid,"task":"MOVE_TO","destination":[420,210]})
    sim.issue_order(u.uid,move)
    visited=[]
    for _ in range(260):
        sim.tick(0.5); visited.append(tuple(u.pos))
        if u.current_order is None and not u.order_queue:break
    assert all(sim.terrain.building_at(p) is None for p in visited)
