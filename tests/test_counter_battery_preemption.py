
from mnsim.scenario import load_scenario
from mnsim.model import Track

def run():
    sim=load_scenario("scenarios/demo.json")
    arty=sim.units["R-ART-1"]
    inf=sim.units["B-INF-2"]
    enemy_arty=sim.units["B-ART-1"]

    # Give artillery both a normal target and a counter-battery target.
    for target,source,cls in [(inf,"SHARED","INFANTRY"),(enemy_arty,"COUNTER_BATTERY","ARTILLERY")]:
        arty.local_tracks[target.uid]=Track(
            track_id=target.uid,target_id=target.uid,estimated_pos=target.pos,position_error_m=10.,
            classification=cls,confidence=.9,last_seen_time=sim.time,
            observations=4,source=source,state="IDENTIFIED",
            belief_confidence=.9,existence_confirmed=True,last_confirmed_time=sim.time
        )
    el,w=next((e,w) for e,w in arty.operational_weapons() if w.capability=="INDIRECT_FIRE")

    assert sim.fire_control.request(arty,inf,el,w,"FIRE_SUPPORT")
    assert sim.fire_control.request(arty,enemy_arty,el,w,"COUNTER_BATTERY")
    assert any(x["kind"]=="FIRE_MISSION_PREEMPTED" and x.get("new_mode")=="COUNTER_BATTERY" for x in sim.logs)
    print("counter-battery mission preemption test: PASS")

if __name__=="__main__": run()
