from mnsim.scenario import load_scenario
from mnsim.formation_geometry import footprint_for, equipment_item_position


def test_equipment_positions_are_spatially_distinct_inside_armor_footprint():
    sim=load_scenario("scenarios/demo.json")
    tank=sim.units["B-TK-1"]
    el=next(e for e in tank.elements.values() if "ARMOR" in e.tags)
    el.ensure_item_states()
    fp=footprint_for(sim,tank)
    pts=[equipment_item_position(tank,el,i,fp) for i in range(len(el.item_states))]
    assert len(set((round(x,3),round(y,3)) for x,y in pts))==len(pts)
    assert fp.length_m>fp.width_m>0


def test_artillery_effect_is_applied_to_spatially_exposed_tank_item():
    sim=load_scenario("scenarios/demo.json")
    shooter=sim.units["R-ART-1"]; tank=sim.units["B-TK-1"]
    el=next(e for e in tank.elements.values() if "ARMOR" in e.tags)
    el.metadata["auto_split_on_mobility_kill"]=False
    el.ensure_item_states(); fp=footprint_for(sim,tank)
    hit_index=0; impact=equipment_item_position(tank,el,hit_index,fp)
    w=next(w for e in shooter.elements.values() for w in e.weapons if w.capability=="INDIRECT_FIRE")
    w.metadata.update({"direct_hit_radius_m":1.0,"near_hit_radius_armor_m":1.1,"fragment_effect_radius_armor_m":1.2,"p_destroy_direct_armor":1.0,"effect_radius_personnel_m":0.1,"fragment_effect_radius_equipment_m":1.2})
    sim.indirect_fire.resolve_impact({"shooter":shooter.uid,"target":tank.uid,"weapon":w.name,"round":1,"pos":impact,"mode":"TEST"})
    sim.time+=1.0
    for ev in sim.events.pop_due(sim.time): sim._handle_event(ev.kind,ev.payload)
    assert el.item_states[hit_index]=="DESTROYED"
    assert sum(s=="DESTROYED" for s in el.item_states)==1


def test_disperse_posture_expands_footprint_and_reduces_exposure():
    sim1=load_scenario("scenarios/demo.json"); u1=sim1.units["B-INF-2"]
    sim2=load_scenario("scenarios/demo.json"); u2=sim2.units["B-INF-2"]
    assert sim2.set_formation_posture(u2,"DISPERSED")
    fp1=footprint_for(sim1,u1); fp2=footprint_for(sim2,u2)
    assert fp2.length_m>fp1.length_m and fp2.width_m>fp1.width_m
    w=next(w for e in sim1.units["B-ART-1"].elements.values() for w in e.weapons if w.capability=="INDIRECT_FIRE")
    w2=next(w for e in sim2.units["B-ART-1"].elements.values() for w in e.weapons if w.capability=="INDIRECT_FIRE")
    el1=next(e for e in u1.elements.values() if e.category=="PERSONNEL")
    el2=next(e for e in u2.elements.values() if e.eid==el1.eid)
    exp1=exp2=0
    for _ in range(80):
        _,a=sim1.indirect_fire._personnel_losses(u1,el1,u1.pos,w,fp1); exp1+=a
        _,b=sim2.indirect_fire._personnel_losses(u2,el2,u2.pos,w2,fp2); exp2+=b
    assert exp2<exp1
