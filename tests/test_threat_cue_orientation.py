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
    sim.combat_config['watch_sweep']={'enabled':False}   # isolate cue expiry from sector scanning
    assert sim._desired_watch_heading(a)==a.watch_heading_deg


def test_halted_unit_without_sector_scans_all_round():
    sim=Simulation(seed=5); a=_unit("A",Side.BLUE,(0,0)); a.watch_heading_deg=0.0
    sim.add_unit(a)
    seen=set()
    for _ in range(12):
        sim._update_watch_heading(a,1.0); seen.add(int(a.watch_heading_deg//45))
    assert len(seen)>=6   # the watch sector sweeps round instead of staring at 0 deg


def test_defender_sweeps_its_assigned_sector():
    from mnsim.model import Order
    sim=Simulation(seed=6); a=_unit("A",Side.BLUE,(0,0)); a.watch_heading_deg=90.0
    a.current_order=Order(order_id="D",kind="HOLD",params={"facing_deg":90.0,"watch_sweep_half_deg":40.0})
    sim.add_unit(a)
    lo=hi=0.0
    for _ in range(20):
        sim._update_watch_heading(a,1.0); d=sim._angle_delta_deg(a.watch_heading_deg,90.0)
        lo=min(lo,d); hi=max(hi,d)
    assert lo<=-35 and 35<=hi<=40.5
