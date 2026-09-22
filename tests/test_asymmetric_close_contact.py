from mnsim.scenario import load_scenario
def run():
    sim=load_scenario("scenarios/demo.json")
    inf=next(u for u in sim.units.values() if u.side.value=="BLUE" and u.branch=="INFANTRY")
    tank=next(u for u in sim.units.values() if u.side.value=="RED" and u.branch=="ARMOR")
    inf.pos=(2000.,2000.); tank.pos=(2005.,2000.)
    for e in inf.elements.values():
        if e.role.upper() in ("ANTI_ARMOR","AT_SPECIALIST"): e.count=0
    sim._sensor_step()
    assert sim._track_for(inf,tank) and sim._track_for(tank,inf)
    assert not sim._unit_can_affect(inf,tank)
    assert sim._unit_can_affect(tank,inf)
    assert any(inf in g and tank in g for g in sim._build_engagements())
    print("asymmetric close-contact structural test: PASS")
if __name__=="__main__": run()
