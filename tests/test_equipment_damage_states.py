from mnsim.scenario import load_scenario


def run():
    sim=load_scenario("scenarios/demo.json")
    tank=sim.units["B-TK-1"]
    el=tank.elements["tanks"]
    el.ensure_item_states()
    before=len(sim.units)

    idx=sim.damage.apply_equipment_effect(tank,el,"MOBILITY_KILL",source="test",weapon="test",reason="TEST")
    assert idx is not None
    # The damaged physical vehicle is transferred out of the parent, so the residual platoon is x3.
    assert el.count==3
    assert el.mobile_item_count==3
    assert len(sim.units)==before+1
    child=sim.units[f"{tank.uid}-DET-1"]
    assert child.metadata["damage_state"]=="MOBILITY_KILL"
    assert child.unit_type.max_speed_mps==0.0
    assert child.elements["tanks"].fire_capable_item_count==1
    assert not child.metadata.get("noncombat_proxy",False)

    # Remaining parent is the real aggregate of the three mobile vehicles.
    assert tank.alive
    print("equipment damage / real mobility-kill detachment test: PASS")

if __name__=="__main__": run()
