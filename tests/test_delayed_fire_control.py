
from mnsim.scenario import load_scenario
from mnsim.model import Track

def run():
    sim=load_scenario("scenarios/demo.json")
    arty=sim.units["B-ART-1"]
    enemy=sim.units["R-INF-1"]
    arty.pos=(500.,500.); enemy.pos=(1600.,1600.)

    # Use deterministic stage delays for this test only.
    c=sim.combat_config
    for k,v in {
        "fire_support_request_delay_min_s":5.0,"fire_support_request_delay_max_s":5.0,
        "fire_direction_compute_delay_min_s":10.0,"fire_direction_compute_delay_max_s":10.0,
        "gun_prepare_delay_min_s":15.0,"gun_prepare_delay_max_s":15.0,
        "artillery_time_of_flight_min_s":8.0,"artillery_time_of_flight_max_s":8.0,
    }.items(): c[k]=v

    # Coordinate observed 12 s before artillery starts processing it.
    sim.time=12.0
    arty.local_tracks[enemy.uid]=Track(
        track_id="delayed",target_id=enemy.uid,estimated_pos=(1600.,1600.),
        position_error_m=20.,classification="INFANTRY",confidence=.9,last_seen_time=0.,
        observations=4,source="SHARED",state="IDENTIFIED",
        belief_confidence=.9,existence_confirmed=True,last_confirmed_time=0.,
    )
    el,w=next((e,w) for e,w in arty.operational_weapons() if w.capability=="INDIRECT_FIRE")
    sim.indirect_fire.fire_mission(arty,enemy,el,w,"FIRE_SUPPORT")
    req=next(x for x in sim.logs if x["kind"]=="FIRE_MISSION_REQUEST")
    assert req["track_age_s"]==12.0
    assert not any(x["kind"]=="INDIRECT_FIRE" for x in sim.logs)

    # 29 s later: still preparing; no firing.
    sim.time=40.9
    for ev in sim.events.pop_due(sim.time): sim._handle_event(ev.kind,ev.payload)
    assert not any(x["kind"]=="INDIRECT_FIRE" for x in sim.logs)

    # At request+30 s the guns fire, but impacts remain delayed by TOF.
    sim.time=42.0
    for ev in sim.events.pop_due(sim.time): sim._handle_event(ev.kind,ev.payload)
    fire=next(x for x in sim.logs if x["kind"]=="INDIRECT_FIRE")
    assert fire["track_age_at_fire_s"] >= 42.0
    assert not any(x["kind"]=="ARTY_IMPACT" for x in sim.logs)

    sim.time=50.0
    for ev in sim.events.pop_due(sim.time): sim._handle_event(ev.kind,ev.payload)
    assert any(x["kind"]=="ARTY_IMPACT" for x in sim.logs)
    print("delayed reporting/fire-control test: PASS")

if __name__=="__main__": run()
