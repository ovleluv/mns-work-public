import json
from pathlib import Path
from mnsim.terrain import TerrainModel
from mnsim.scenario import load_scenario

ROOT=Path(__file__).resolve().parents[1]

def test_destroyed_bridge_is_no_longer_crossing_or_route_candidate():
    data=json.loads((ROOT/'config/terrain_demo.json').read_text())
    tm=TerrainModel(data); br=tm.bridge_by_id('BR1'); p=tuple(br['center'])
    assert tm.on_bridge(p)
    br['integrity']=0; br['destroyed']=True
    assert not tm.on_bridge(p)
    assert tm.nearest_bridge((2000,2500),(2100,3300)) != p

def test_bridge_damage_accumulates_and_can_destroy():
    import random
    data=json.loads((ROOT/'config/terrain_demo.json').read_text()); tm=TerrainModel(data); br=tm.bridge_by_id('BR1')
    md={'bridge_deck_damage_min':50,'bridge_deck_damage_max':50,'bridge_near_effect_m':18}
    for _ in range(3): tm.apply_bridge_impact(tuple(br['center']),md,random.Random(1))
    assert br['destroyed'] and br['integrity']==0

def test_demo_bridge_artillery_has_sequential_infrastructure_order():
    sim=load_scenario(str(ROOT/'scenarios/demo.json')); u=sim.units['B-ART-2']
    assert u.order_queue[0].kind=='STRIKE_INFRASTRUCTURE'
    assert u.order_queue[0].params['targets']==['BR1','BR2']
