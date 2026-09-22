
from mnsim.scenario import load_scenario

def run():
    sim=load_scenario("scenarios/demo.json")
    tank=sim.units["B-TK-1"]
    arty=sim.units["B-ART-1"]

    # Neither current tracked tank nor wheeled/towed artillery has a water-crossing capability.
    assert not sim.terrain.passable(tank,(1000,2835))
    assert not sim.terrain.passable(arty,(1000,2835))
    # Bridge is passable.
    assert sim.terrain.passable(tank,(2050,2835))
    assert sim.terrain.passable(arty,(2050,2835))

    # Wheeled/towed gets a much larger road benefit than tracked armor.
    road=(2050,2050)
    open_ground=(1000,1000)
    assert sim.terrain.speed_factor(arty,road) > sim.terrain.speed_factor(arty,open_ground)
    road_gain_arty=sim.terrain.speed_factor(arty,road)/sim.terrain.speed_factor(arty,open_ground)
    road_gain_tank=sim.terrain.speed_factor(tank,road)/sim.terrain.speed_factor(tank,open_ground)
    assert road_gain_arty > road_gain_tank

    # A direct route across the river should be redirected to a bridge.
    tank.pos=(1500,2500)
    waypoint=sim.terrain.movement_target(tank,(1500,3200))
    # Sparse A* now returns an approach portal rather than teleporting the formation to bridge center.
    assert min(abs(waypoint[0]-2050),abs(waypoint[0]-3300)) < 5.0
    assert waypoint[1] < 2835
    print("terrain mobility/bridge test: PASS")

if __name__=="__main__": run()
