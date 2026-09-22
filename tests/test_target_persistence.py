
from mnsim.scenario import load_scenario
from mnsim.model import Track

def run():
    sim=load_scenario("scenarios/demo.json")
    shooter=sim.units["B-TK-1"]
    e1=sim.units["R-INF-1"]; e2=sim.units["R-INF-2"]
    shooter.pos=(2000.,2000.); e1.pos=(2300.,1980.); e2.pos=(2320.,2020.)
    for e in (e1,e2):
        shooter.local_tracks[e.uid]=Track(
            track_id=e.uid,target_id=e.uid,estimated_pos=e.pos,position_error_m=1.,
            classification="INFANTRY",confidence=.95,last_seen_time=sim.time,
            observations=5,source="LOCAL",state="IDENTIFIED",
            belief_confidence=.95,existence_confirmed=True,last_confirmed_time=sim.time
        )

    first=sim.combat.select_target(shooter,[e1,e2])
    assert first is not None
    initial=first.uid

    # Repeated calls during lock interval must not randomly flip targets.
    for _ in range(100):
        sim.time += 0.1
        t=sim.combat.select_target(shooter,[e1,e2])
        assert t.uid==initial

    # Even after lock time, a merely marginally better target should not trigger a switch.
    sim.time += sim.combat_config["target_lock_min_s"] + 1
    t=sim.combat.select_target(shooter,[e1,e2])
    assert t.uid==initial
    print("target persistence/hysteresis test: PASS")

if __name__=="__main__": run()
