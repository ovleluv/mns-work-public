from mnsim.scenario import load_scenario
from mnsim.model import UnitState


def test_mobility_kill_transfers_vehicle_to_real_stationary_combat_entity():
    sim=load_scenario("scenarios/demo.json")
    tank=sim.units["B-TK-1"]
    el=tank.elements["tanks"]
    el.ensure_item_states()
    start_pos=tank.pos
    original_initial=el.initial_count

    idx=sim.damage.apply_equipment_effect(tank,el,"MOBILITY_KILL",source="test",weapon="test",reason="TEST")
    assert idx is not None
    assert el.count==3
    assert el.initial_count==original_initial-1
    assert el.mobile_item_count==3
    assert el.fire_capable_item_count==3

    child=sim.units["B-TK-1-DET-1"]
    cel=child.elements["tanks"]
    assert child.metadata.get("actual_detached_vehicle") is True
    assert not child.metadata.get("noncombat_proxy",False)
    assert child.unit_type.max_speed_mps==0.0
    assert child.pos==start_pos
    assert cel.count==1
    assert cel.item_states==["MOBILITY_KILL"]
    assert cel.mobile_item_count==0
    assert cel.fire_capable_item_count==1
    assert child.operational_weapons(), "mobility-killed tank must retain surviving weapons"
    assert child in sim._direct_units(), "detached vehicle must participate in direct combat"


def test_two_mobility_kills_leave_two_vehicle_residual_platoon_and_two_stationary_children():
    sim=load_scenario("scenarios/demo.json")
    tank=sim.units["B-TK-1"]
    el=tank.elements["tanks"]
    full_mobile_factor=sim.terrain.speed_factor(tank,tank.pos)
    sim.damage.apply_equipment_effect(tank,el,"MOBILITY_KILL",source="test",weapon="test",reason="TEST1")
    sim.damage.apply_equipment_effect(tank,el,"MOBILITY_KILL",source="test",weapon="test",reason="TEST2")
    assert el.count==2
    assert el.initial_count==2
    assert el.mobile_item_count==2
    assert "B-TK-1-DET-1" in sim.units and "B-TK-1-DET-2" in sim.units
    assert all(sim.units[x].unit_type.max_speed_mps==0.0 for x in ("B-TK-1-DET-1","B-TK-1-DET-2"))

    # Detached vehicles must not reduce the residual platoon's mobility factor: the parent now
    # physically owns two mobile tanks, not four tanks with two zero-mobility passengers.
    assert abs(sim.terrain.speed_factor(tank,tank.pos)-full_mobile_factor) < 1e-9
    equipment=[e for e in tank.elements.values() if e.category.upper()=="EQUIPMENT" and "ARMOR" in e.tags]
    assert sum(e.mobile_item_count for e in equipment)==sum(e.count for e in equipment)==2

    parent_start=tank.pos
    child=sim.units["B-TK-1-DET-1"]
    child_start=child.pos
    destination=(parent_start[0]+300.0,parent_start[1])
    tank.state=UnitState.MOVING
    child.state=UnitState.MOVING
    sim._move_toward(tank,destination,5.0)
    sim._move_toward(child,destination,5.0)
    assert tank.pos != parent_start, "residual platoon must continue moving"
    assert child.pos == child_start, "mobility-killed vehicle must remain at the damage location"


def test_disabled_detached_vehicle_is_stationary_and_cannot_fire():
    sim=load_scenario("scenarios/demo.json")
    tank=sim.units["B-TK-1"]
    el=tank.elements["tanks"]
    sim.damage.apply_equipment_effect(tank,el,"DISABLED",source="test",weapon="test",reason="TEST")
    child=sim.units["B-TK-1-DET-1"]
    cel=child.elements["tanks"]
    assert cel.item_states==["DISABLED"]
    assert cel.mobile_item_count==0
    assert cel.fire_capable_item_count==0
    assert child.operational_weapons()==[]
    assert child.unit_type.max_speed_mps==0.0


def test_detached_vehicle_can_be_damaged_without_splitting_again():
    sim=load_scenario("scenarios/demo.json")
    tank=sim.units["B-TK-1"]
    el=tank.elements["tanks"]
    sim.damage.apply_equipment_effect(tank,el,"MOBILITY_KILL",source="test",weapon="test",reason="TEST")
    child=sim.units["B-TK-1-DET-1"]
    cel=child.elements["tanks"]
    before=len(sim.units)
    sim.damage.apply_equipment_effect(child,cel,"FIREPOWER_KILL",source="test",weapon="test",reason="SECOND_HIT")
    assert len(sim.units)==before
    assert cel.item_states[0]=="DISABLED"


def test_mobility_killed_detached_vehicle_can_continue_direct_fire_from_damage_location():
    from mnsim.model import Track
    sim=load_scenario("scenarios/demo.json")
    tank=sim.units["B-TK-1"]
    el=tank.elements["tanks"]
    sim.damage.apply_equipment_effect(tank,el,"MOBILITY_KILL",source="test",weapon="test",reason="TEST")
    child=sim.units["B-TK-1-DET-1"]
    enemy=sim.units["R-TK-1"]
    enemy.pos=(child.pos[0]+300.0,child.pos[1])
    child.local_tracks[enemy.uid]=Track(
        track_id=f"{child.uid}:{enemy.uid}",target_id=enemy.uid,estimated_pos=enemy.pos,
        position_error_m=0.0,classification="ARMOR",confidence=1.0,last_seen_time=sim.time,
        observations=5,source="LOCAL",state="IDENTIFIED",belief_confidence=1.0,
        existence_confirmed=True,last_confirmed_time=sim.time)
    child.target_id=enemy.uid
    child.metadata["target_acquired_t"]=sim.time

    sim.combat.fire_all(child,enemy,"DIRECT")  # starts acquisition/lay
    sim.time += 10.0
    sim.combat.fire_all(child,enemy,"DIRECT")
    assert any(r.get("kind")=="FIRE" and r.get("shooter")==child.uid for r in sim.logs), \
        "mobility-killed detached tank should be able to fire surviving weapons"


def test_detaching_last_vehicle_leaves_no_ghost_parent_equipment():
    sim=load_scenario("scenarios/demo.json")
    tank=sim.units["B-TK-1"]
    # Transfer all four tanks out through mobility kills.  The residual aggregate must disappear;
    # an empty item_states list must never regenerate a phantom x1 vehicle.
    for n in range(4):
        el=tank.elements["tanks"]
        assert el.count == 4-n
        sim.damage.apply_equipment_effect(tank,el,"MOBILITY_KILL",source="test",weapon="test",reason=f"TEST{n}")
    el=tank.elements["tanks"]
    assert el.item_states == []
    assert el.count == 0
    assert tank.equipment == 0
    assert not tank.active
    assert not tank.alive
    assert len([u for u in sim.units.values() if u.metadata.get("detached_from")==tank.uid]) == 4
    # Even if a caller asks for equipment state later, no physical item may be recreated.
    el.ensure_item_states()
    assert el.item_states == [] and el.count == 0
