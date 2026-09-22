import json
from mnsim.scenario import load_scenario
from mnsim.model import Track


def _sim(tmp_path, profile):
    toe={"unit_types":{
      "MIXED":{"branch":"INFANTRY","max_speed_mps":2.0,"detection_range_m":800,
        "metadata":{"echelon":"PLT","formation_family":"INF"},
        "elements":[{"id":"rifle","category":"PERSONNEL","role":"RIFLE","count":8,"weapons":[
          {"name":"rifle","range_m":300,"pk":0.1,"capability":"ANTI_PERSONNEL","target_tags":["PERSONNEL"]},
          {"name":"mg","range_m":700,"pk":0.1,"capability":"ANTI_PERSONNEL","target_tags":["PERSONNEL"]}]}]},
      "TARGET":{"branch":"INFANTRY","max_speed_mps":0,"detection_range_m":500,
        "metadata":{"echelon":"PLT","formation_family":"INF"},
        "elements":[{"id":"p","category":"PERSONNEL","role":"RIFLE","count":8,"weapons":[]}]}
    }}
    (tmp_path/'toe.json').write_text(json.dumps(toe))
    (tmp_path/'terrain.json').write_text('{}')
    (tmp_path/'doctrine.json').write_text(json.dumps({"profiles":{
      "STAND":{"engagement_range_policy":"STANDOFF","engagement_range_fraction":1.0},
      "AGG":{"engagement_range_policy":"COMBINED_ARMS","engagement_range_fraction":0.9}}}))
    sc={"seed":1,"world":{"width_m":2000,"height_m":1000},"terrain_file":"terrain.json","unit_types_file":"toe.json","doctrine_profiles_file":"doctrine.json",
        "units":[{"id":"B","side":"BLUE","type":"MIXED","echelon":"PLT","pos":[100,100],"doctrine_profile":profile},
                 {"id":"R","side":"RED","type":"TARGET","echelon":"PLT","pos":[600,100]}]}
    (tmp_path/'scenario.json').write_text(json.dumps(sc))
    sim=load_scenario(str(tmp_path/'scenario.json'))
    b,r=sim.units['B'],sim.units['R']
    tr=Track(track_id='B:R',target_id='R',estimated_pos=r.pos,position_error_m=0,classification='PERSONNEL',confidence=1,last_seen_time=0,observations=2,source='LOCAL',state='IDENTIFIED',belief_confidence=1,existence_confirmed=True,last_confirmed_time=0)
    b.local_tracks={'R':tr}
    return sim,b,r,tr


def test_standoff_uses_longest_relevant_weapon(tmp_path):
    sim,b,r,tr=_sim(tmp_path,'STAND')
    assert sim.doctrine.desired_direct_engagement_range(b,r)==700
    assert sim.doctrine.should_close_for_direct_fire(b,r,tr) is False


def test_aggressive_closes_until_shortest_relevant_weapon_can_join(tmp_path):
    sim,b,r,tr=_sim(tmp_path,'AGG')
    assert sim.doctrine.desired_direct_engagement_range(b,r)==270
    assert sim.doctrine.should_close_for_direct_fire(b,r,tr) is True
