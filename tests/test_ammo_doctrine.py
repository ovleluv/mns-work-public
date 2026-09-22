from mnsim.scenario import load_scenario
def run():
    sim=load_scenario("scenarios/demo.json")
    inf=next(u for u in sim.units.values() if u.side.value=="BLUE" and u.branch=="INFANTRY")
    at=inf.elements["at_specialist"]
    atw=next(w for w in at.weapons if w.capability=="ANTI_ARMOR")
    assert atw.ammo_remaining==3
    assert atw.expend_round() and atw.ammo_remaining==2
    assert atw.expend_round() and atw.expend_round() and atw.ammo_remaining==0
    assert not inf.capability_available("ANTI_ARMOR")
    assert any(w.capability=="ANTI_PERSONNEL" for _,w in inf.operational_weapons())
    print("ammo/doctrine inventory test: PASS")
if __name__=="__main__": run()
