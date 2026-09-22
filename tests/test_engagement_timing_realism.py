import math
from mnsim.scenario import load_scenario
from mnsim.model import Track


def _track(observer, target, now=0.0, confidence=.95, error=5.0):
    observer.local_tracks[target.uid] = Track(
        track_id=f"{observer.uid}:{target.uid}", target_id=target.uid,
        estimated_pos=target.pos, position_error_m=error, classification=target.branch,
        confidence=confidence, last_seen_time=now, observations=5, source="LOCAL",
        state="IDENTIFIED", belief_confidence=confidence, existence_confirmed=True,
        last_confirmed_time=now,
    )


def test_direct_fire_requires_acquisition_delay():
    sim=load_scenario("scenarios/demo.json")
    shooter=sim.units["B-INF-2"]; target=sim.units["R-INF-1"]
    shooter.pos=(1000.,1000.); target.pos=(1200.,1000.)
    sim.time=10.0; _track(shooter,target,sim.time)
    shooter.target_id=target.uid; shooter.metadata["target_acquired_t"]=sim.time
    el=shooter.elements["rifle_1"]; w=el.weapons[0]

    sim.combat.fire_weapon(shooter,target,el,w)
    assert any(x["kind"]=="DIRECT_FIRE_ACQUIRING" for x in sim.logs)
    assert not any(x["kind"]=="FIRE" for x in sim.logs)

    sim.time += float(w.metadata["acquisition_delay_max_s"]) + .1
    sim.combat.fire_weapon(shooter,target,el,w)
    assert any(x["kind"]=="FIRE" for x in sim.logs)


def test_artillery_respects_weapon_cycle_floor():
    sim=load_scenario("scenarios/demo.json")
    arty=sim.units["B-ART-1"]; target=sim.units["R-INF-1"]
    arty.pos=(500.,500.); target.pos=(1500.,500.)
    sim.time=0.0; _track(arty,target,0.0)
    c=sim.combat_config
    for k in ("fire_support_request_delay_min_s","fire_support_request_delay_max_s",
              "fire_direction_compute_delay_min_s","fire_direction_compute_delay_max_s",
              "gun_prepare_delay_min_s","gun_prepare_delay_max_s"):
        c[k]=0.0
    c["artillery_reload_delay_min_s"]=1.0; c["artillery_reload_delay_max_s"]=1.0
    el,w=next((e,w) for e,w in arty.operational_weapons() if w.capability=="INDIRECT_FIRE")
    assert sim.fire_control.request(arty,target,el,w,"FIRE_SUPPORT")
    for ev in sim.events.pop_due(sim.time):
        sim._handle_event(ev.kind,ev.payload)
    key=(arty.uid,f"{el.eid}:{w.name}")
    assert sim.fire_control.next_available[key] >= 60.0/w.shots_per_min - 1e-6


def test_movement_planning_speeds_are_tactical_not_sprint_speed():
    sim=load_scenario("scenarios/demo.json")
    inf=sim.units["B-INF-2"]; tank=sim.units["B-TK-1"]; arty=sim.units["B-ART-1"]
    # Dismounted road planning speed ~4 km/h; open-country slower.
    assert math.isclose(inf.unit_type.max_speed_mps*3.6, 4.0, rel_tol=.03)
    assert sim.terrain.speed_factor(inf,(1000,1000)) < sim.terrain.speed_factor(inf,(2050,2050))
    # Tactical vehicle formation speeds remain well above dismounted speed without using brochure top speed.
    assert tank.unit_type.max_speed_mps > inf.unit_type.max_speed_mps
    assert arty.unit_type.max_speed_mps > inf.unit_type.max_speed_mps
