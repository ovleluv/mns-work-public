from mnsim.simulation import Simulation
from mnsim.model import Unit, UnitType, Side, FormationElement, WeaponModel

def _unit(uid, side, pos, branch="INFANTRY"):
    ut=UnitType(name=branch, branch=branch, max_speed_mps=1.0, detection_range_m=900.0)
    el=FormationElement(eid=f"{uid}-e",name="rifle team",role="rifle",category="PERSONNEL",count=4,initial_count=4,weapons=[WeaponModel(name="rifle",range_m=350,pk=.2,shots_per_min=5,capability="ANTI_PERSONNEL")])
    return Unit(uid=uid,name=uid,side=side,echelon="PLT",unit_type=ut,pos=pos,elements={el.eid:el})

def test_incoming_fire_cue_turns_watch_without_creating_track():
    sim=Simulation(seed=3); a=_unit("A",Side.BLUE,(0,0)); b=_unit("B",Side.RED,(100,0)); a.watch_heading_deg=180.0
    sim.add_unit(a); sim.add_unit(b); sim._register_threat_cue(a,b.uid,"DIRECT_FIRE")
    assert b.uid not in a.local_tracks
    assert abs(sim._angle_delta_deg(sim._desired_watch_heading(a),0.0)) < 45.0
    sim._update_watch_heading(a,1.0)
    assert abs(sim._angle_delta_deg(a.watch_heading_deg,180.0)) > 1.0

def test_threat_cue_expires():
    sim=Simulation(seed=4); a=_unit("A",Side.BLUE,(0,0)); b=_unit("B",Side.RED,(100,0)); a.watch_heading_deg=90.0
    sim.add_unit(a); sim.add_unit(b); sim._register_threat_cue(a,b.uid,"DIRECT_FIRE")
    sim.time=float(a.metadata["threat_cue_until_t"])+0.1
    assert sim._desired_watch_heading(a)==a.watch_heading_deg
