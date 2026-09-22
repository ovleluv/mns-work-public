from mnsim.scenario import load_scenario


def test_astar_routes_non_amphibious_unit_through_operational_bridge():
    sim=load_scenario("scenarios/demo.json")
    tank=sim.units["B-TK-1"]
    tank.pos=(1500.0,2500.0)
    route=sim.terrain.plan_route(tank,(1500.0,3200.0))
    assert len(route)>=3
    # The route should contain a bridge crossing segment (portal-to-portal through river water).
    assert any(abs(a[0]-b[0]) < 150.0 and a[1] < 2760.0 and b[1] > 2910.0
               for a,b in zip(route,route[1:]))


def test_destroyed_bridge_is_removed_from_future_routes():
    sim=load_scenario("scenarios/demo.json")
    tank=sim.units["B-TK-1"]
    tank.pos=(2050.0,2500.0)
    br1=sim.terrain.bridge_by_id("BR1"); br1["destroyed"]=True; br1["integrity"]=0.0
    route=sim.terrain.plan_route(tank,(2050.0,3200.0))
    assert route and len(route)>1
    assert any(p[0] > 3000.0 for p in route[1:-1])


def test_wheeled_formation_can_choose_longer_road_route_by_travel_time():
    sim=load_scenario("scenarios/demo.json")
    arty=sim.units["B-ART-1"]
    arty.pos=(250.0,1800.0)
    route=sim.terrain.plan_route(arty,(3800.0,2200.0))
    assert len(route)>2
    assert any(sim.terrain.on_road(p) for p in route[1:-1])
