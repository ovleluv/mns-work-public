import json
from pathlib import Path
from mnsim.scenario import load_scenario


def test_attack_position_does_not_stall_on_building_corner(tmp_path):
    terrain = {
        "roads": [], "rivers": [], "bridges": [],
        "areas": [{"id":"BLD3","type":"BUILDING","polygon":[[2453.3,1337.8],[1728.9,1288.9],[1777.8,1573.3],[2448.9,1551.1]],"integrity":180.0,"max_integrity":180.0,"height_m":5.0}]
    }
    scenario = {
        "seed":7,"world":{"width_m":4000,"height_m":4000},"objectives":{},
        "units":[
            {"id":"B-INF_PLT-1","side":"BLUE","echelon":"PLT","type":"INF_PLT","pos":[1737.8,475.6],"heading_deg":0.0,
             "orders":[{"id":"B-HOLD","kind":"HOLD","params":{"duration_s":9999}}]},
            {"id":"R-INF_PLT-1","side":"RED","echelon":"PLT","type":"INF_PLT","pos":[2026.7,1817.8],"heading_deg":0.0,
             "orders":[{"id":"R-HOLD","kind":"HOLD","params":{"duration_s":9999}}]}
        ],"aggregations":[],"terrain_file":"terrain.json"
    }
    bml={"side":"BLUE","replace_existing_orders":True,"missions":[{"id":"BLUE-ATTACK-POS-1","unit":"B-INF_PLT-1","task":"ATTACK_POSITION","destination":[2026.7,1817],"persistent":False}]}
    (tmp_path/'terrain.json').write_text(json.dumps(terrain),encoding='utf-8')
    (tmp_path/'scenario.json').write_text(json.dumps(scenario),encoding='utf-8')
    (tmp_path/'blue.json').write_text(json.dumps(bml),encoding='utf-8')
    sim=load_scenario(str(tmp_path/'scenario.json'), {'BLUE':str(tmp_path/'blue.json')})
    u=sim.units['B-INF_PLT-1']
    max_blocked=0; blocked=0
    for _ in range(12000):
        sim.tick(0.25)
        if u.metadata.get('tactical_reason')=='TERRAIN BLOCKED / SEEKING BRIDGE':
            blocked+=1; max_blocked=max(max_blocked,blocked)
        else:
            blocked=0
    assert max_blocked < 20
    assert u.pos[1] > 1565.0
    assert u.metadata.get('tactical_reason') != 'TERRAIN BLOCKED / SEEKING BRIDGE'
