
from mnsim.scenario import load_scenario
from mnsim.model import Track

def run():
    sim=load_scenario("scenarios/demo.json")
    blue=sim.units["B-ART-1"]
    red=sim.units["R-ART-1"]
    tr=Track(track_id="belief-test",target_id=red.uid,estimated_pos=red.pos,
             position_error_m=80,classification="ARTILLERY",confidence=.8,last_seen_time=0,
             observations=2,source="COUNTER_BATTERY",state="CLASSIFIED",
             belief_confidence=.8,existence_confirmed=True,last_confirmed_time=0)
    blue.local_tracks[red.uid]=tr

    # Age beyond firing-track lifetime. Tactical track is lost, but enemy OOB belief remains displayed.
    sim.time=180.0
    sim._next_sensor_update=999999
    sim.belief.age(tr)
    tr.state="LOST"; tr.confidence=.1
    assert sim._track_for(blue,red) is None
    merged=sim.side_tracks(blue.side)
    assert red.uid in merged
    assert merged[red.uid].belief_confidence >= sim.combat_config["belief_display_threshold"]

    # Explicit terminal/BDA evidence removes the persistent belief.
    sim.belief.mark_destroyed(tr)
    assert red.uid not in sim.side_tracks(blue.side)
    print("persistent enemy-belief test: PASS")

if __name__=="__main__": run()
