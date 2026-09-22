from mnsim.scenario import load_scenario
from mnsim.model import Track


def _track(target, now, cls, source="SHARED", conf=.85, err=25.0):
    return Track(track_id=target.uid,target_id=target.uid,estimated_pos=target.pos,
                 position_error_m=err,classification=cls,confidence=conf,
                 last_seen_time=now,observations=3,source=source,state="IDENTIFIED",
                 belief_confidence=conf,existence_confirmed=True,last_confirmed_time=now)


def test_counterbattery_track_survives_generic_visual_lost_window():
    sim=load_scenario("scenarios/demo.json")
    arty=sim.units["B-ART-1"]; enemy=sim.units["R-ART-1"]
    arty.local_tracks[enemy.uid]=_track(enemy,0.0,"ARTILLERY","COUNTER_BATTERY",.8,60.0)
    sim.time=70.0
    sim._sensor_step()
    tr=arty.local_tracks[enemy.uid]
    assert tr.state != "LOST"
    assert sim._track_for(arty,enemy,"COUNTER_BATTERY") is not None


def test_counterbattery_track_degrades_to_belief_guided_counterfire():
    sim=load_scenario("scenarios/demo.json")
    arty=sim.units["B-ART-1"]; enemy=sim.units["R-ART-1"]
    arty.local_tracks[enemy.uid]=_track(enemy,0.0,"ARTILLERY","COUNTER_BATTERY",.8,60.0)
    sim.time=181.0
    sim._sensor_step()
    assert arty.local_tracks[enemy.uid].state == "LOST"
    inferred=sim._track_for(arty,enemy,"COUNTER_BATTERY")
    assert inferred is not None
    assert inferred.state == "INFERRED"
    assert inferred.position_error_m > 60.0
    sim.time=901.0
    assert sim._track_for(arty,enemy,"COUNTER_BATTERY") is None


def test_indirect_target_priority_prefers_artillery_when_tracks_are_comparable():
    sim=load_scenario("scenarios/demo.json")
    arty=sim.units["B-ART-1"]
    enemy_arty=sim.units["R-ART-1"]; enemy_inf=sim.units["R-INF-2"]
    # Put perceived positions at similar distance so doctrine priority dominates geometry.
    enemy_arty_pos=(arty.pos[0]+1200,arty.pos[1]); enemy_inf_pos=(arty.pos[0]+1200,arty.pos[1]+10)
    ta=_track(enemy_arty,sim.time,"ARTILLERY","COUNTER_BATTERY",.85,30.0); ta.estimated_pos=enemy_arty_pos
    ti=_track(enemy_inf,sim.time,"INFANTRY","SHARED",.85,30.0); ti.estimated_pos=enemy_inf_pos
    arty.local_tracks[enemy_arty.uid]=ta; arty.local_tracks[enemy_inf.uid]=ti
    assert sim.combat.target_score(arty,enemy_arty,"COUNTER_BATTERY") > sim.combat.target_score(arty,enemy_inf,"FIRE_SUPPORT")


def test_target_score_uses_perceived_classification_not_ground_truth_branch():
    sim=load_scenario("scenarios/demo.json")
    shooter=sim.units["B-TK-1"]; target=sim.units["R-INF-1"]
    tr=_track(target,sim.time,"ARMOR","SHARED",.9,15.0)
    shooter.local_tracks[target.uid]=tr
    armor_score=sim.combat.target_score(shooter,target,"DIRECT")
    tr.classification="INFANTRY"
    infantry_score=sim.combat.target_score(shooter,target,"DIRECT")
    assert armor_score > infantry_score
