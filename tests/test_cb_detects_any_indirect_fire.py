
from mnsim.scenario import load_scenario
from mnsim.model import Track

def run():
    sim=load_scenario("scenarios/demo.json")
    red_arty=sim.units["R-ART-1"]
    blue_inf=sim.units["B-INF-2"]
    blue_radar=sim.units["B-ART-1"]

    # Keep the red firing point within BLUE radar range.
    assert blue_radar.distance_to(red_arty) <= blue_radar.elements["cb_radar"].metadata["radar_range_m"]

    # Give RED artillery a firing-quality track on ordinary BLUE infantry.
    red_arty.local_tracks[blue_inf.uid]=Track(
        track_id="fire-support-target", target_id=blue_inf.uid,
        estimated_pos=blue_inf.pos, position_error_m=1.0,
        classification="INFANTRY", confidence=.99, last_seen_time=sim.time,
        observations=10, source="LOCAL", state="IDENTIFIED",
        belief_confidence=.99, existence_confirmed=True, last_confirmed_time=sim.time,
    )

    gun_el,weapon=next((e,w) for e,w in red_arty.operational_weapons() if w.capability=="INDIRECT_FIRE")

    # Make radar detection deterministic for this architecture test only.
    radar=blue_radar.elements["cb_radar"]
    radar.metadata["detect_p_near"]=1.0
    radar.metadata["detect_p_edge"]=1.0
    radar.metadata["processing_delay_min_s"]=0.0
    radar.metadata["processing_delay_max_s"]=0.0

    # Eliminate fire-control preparation delay only inside this unit test.
    for k in ("fire_support_request_delay_min_s","fire_support_request_delay_max_s",
              "fire_direction_compute_delay_min_s","fire_direction_compute_delay_max_s",
              "gun_prepare_delay_min_s","gun_prepare_delay_max_s"):
        sim.combat_config[k]=0.0
    sim.indirect_fire.fire_mission(red_arty,blue_inf,gun_el,weapon,"FIRE_SUPPORT")
    for ev in list(sim.events.pop_due(sim.time)):
        sim._handle_event(ev.kind,ev.payload)
    assert any(x["kind"]=="INDIRECT_LAUNCH" and x.get("mode")=="FIRE_SUPPORT" for x in sim.logs)
    assert any(x["kind"]=="CB_RADAR_DETECTION_PENDING" and x.get("source")==red_arty.uid for x in sim.logs)

    for ev in list(sim.events.pop_due(sim.time)):
        sim._handle_event(ev.kind,ev.payload)
    assert any(x["kind"]=="CB_RADAR_DETECT" and x.get("source")==red_arty.uid for x in sim.logs)
    print("counter-battery detects any indirect-fire launch: PASS")

if __name__=="__main__": run()
