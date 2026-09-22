from mnsim.scenario import load_scenario
from mnsim.model import Track


def test_artillery_can_re_attack_old_cb_belief_after_radar_is_disabled():
    sim=load_scenario("scenarios/demo.json")
    arty=sim.units["R-ART-1"]; enemy=sim.units["B-ART-1"]
    tr=Track(track_id="old-cb",target_id=enemy.uid,estimated_pos=enemy.pos,position_error_m=70.0,
             classification="ARTILLERY",confidence=.72,last_seen_time=0.0,observations=2,
             source="COUNTER_BATTERY",state="IDENTIFIED",belief_confidence=.75,
             existence_confirmed=True,last_confirmed_time=0.0)
    arty.local_tracks[enemy.uid]=tr
    # Kill RED's radar hardware after the location has already been disseminated.
    for u in sim.units.values():
        if u.side==arty.side:
            for el in u.elements.values():
                if el.role.upper()=="COUNTER_BATTERY_RADAR":
                    if el.item_states:
                        el.item_states=["DESTROYED" for _ in el.item_states]
                    else:
                        el.count=0
    sim.time=300.0
    inferred=sim._track_for(arty,enemy,"COUNTER_BATTERY")
    assert inferred is not None and inferred.state=="INFERRED"
    assert inferred.position_error_m>tr.position_error_m
    # Perception-driven indirect eligibility must not depend on the target's hidden operational state.
    assert sim._unit_can_affect(arty,enemy,"COUNTER_BATTERY")
    sim._combat_step()
    assert any(x["kind"]=="FIRE_MISSION_REQUEST" and x.get("shooter")==arty.uid and x.get("mode")=="COUNTER_BATTERY" for x in sim.logs)
