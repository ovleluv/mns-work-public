
from mnsim.scenario import load_scenario
from mnsim.combat import CombatResolver
from mnsim.doctrine import DoctrineEngine

def run():
    sim = load_scenario("scenarios/demo.json")
    assert isinstance(sim.combat, CombatResolver)
    assert isinstance(sim.doctrine, DoctrineEngine)

    tank = next(u for u in sim.units.values() if u.branch=="ARMOR")
    infantry = next(u for u in sim.units.values() if u.branch=="INFANTRY")
    tank_ap = [w for _,w in tank.operational_weapons() if w.capability=="ANTI_PERSONNEL"]
    assert len(tank_ap) >= 2, "tank should have both HE/APERS and machine-gun style anti-personnel fires"
    assert any("machine gun" in w.name.lower() for w in tank_ap)

    at_el = infantry.elements["at_specialist"]
    atw = next(w for w in at_el.weapons if w.capability=="ANTI_ARMOR")
    while atw.ammo_remaining > 0:
        atw.expend_round()
    assert not infantry.capability_available("ANTI_ARMOR")
    assert any(w.capability=="ANTI_PERSONNEL" for _,w in infantry.operational_weapons())
    print("modular combat/doctrine test: PASS")

if __name__=="__main__":
    run()
