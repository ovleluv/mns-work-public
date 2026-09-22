
from mnsim.scenario import load_scenario
from mnsim.model import Track

def run():
    sim=load_scenario("scenarios/demo.json")
    arty=next(u for u in sim.units.values() if u.side.value=="BLUE" and u.branch=="ARTILLERY")
    enemy=next(u for u in sim.units.values() if u.side.value=="RED" and u.branch=="INFANTRY")

    # Put target well inside artillery range and provide a high-quality perceived track.
    arty.pos=(500.0,500.0)
    enemy.pos=(1800.0,1800.0)
    arty.local_tracks[enemy.uid]=Track(
        track_id=f"{arty.uid}:{enemy.uid}", target_id=enemy.uid,
        estimated_pos=enemy.pos, position_error_m=1.0,
        classification="INFANTRY", confidence=0.99, last_seen_time=sim.time,
        observations=10, source="LOCAL", state="IDENTIFIED"
    )
    el,w=next((e,w) for e,w in arty.operational_weapons() if w.capability=="INDIRECT_FIRE")
    # Force tight dispersion/effect for deterministic architecture test rather than performance calibration.
    w.metadata["dispersion_cep_m"]=1.0
    w.metadata["effect_radius_personnel_m"]=120.0
    w.metadata["effect_p_personnel"]=0.99
    w.metadata["max_personnel_loss_per_round"]=2

    before=enemy.personnel
    for k in ("fire_support_request_delay_min_s","fire_support_request_delay_max_s",
              "fire_direction_compute_delay_min_s","fire_direction_compute_delay_max_s",
              "gun_prepare_delay_min_s","gun_prepare_delay_max_s",
              "artillery_time_of_flight_min_s","artillery_time_of_flight_max_s"):
        sim.combat_config[k]=0.0
    sim.indirect_fire.fire_mission(arty,enemy,el,w,"FIRE_SUPPORT")
    for ev in list(sim.events.pop_due(sim.time)):
        sim._handle_event(ev.kind,ev.payload)
    for ev in list(sim.events.pop_due(sim.time)):
        sim._handle_event(ev.kind,ev.payload)
    assert any(x["kind"]=="INDIRECT_FIRE" for x in sim.logs)
    assert sum(1 for x in sim.logs if x["kind"]=="ARTY_IMPACT") == el.count
    # Apply queued casualties.
    sim.time += 1.0
    for ev in sim.events.pop_due(sim.time):
        sim._handle_event(ev.kind,ev.payload)
    assert enemy.personnel < before
    print("indirect-fire spatial model test: PASS")

if __name__=="__main__": run()
