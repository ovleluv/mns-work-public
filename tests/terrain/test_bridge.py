"""다리: 진입·이탈, 역방향, 파괴 후 캐시 무효화."""
import pytest
from .support import DT, START, GOAL, install, river, bridge, bridge_corridor, run

@pytest.mark.parametrize("dt",DT)
@pytest.mark.parametrize("reverse",[False,True])
def test_live_bridge_arrival(mover,record,dt,reverse):
    sim,u=mover
    install(sim,{"rivers":[river()],"bridges":[bridge()]})
    a,b=(GOAL,START) if reverse else (START,GOAL)
    run(sim,u,record,dt,start=a,goal=b,check=bridge_corridor)

@pytest.mark.parametrize("dt",DT)
def test_destroyed_bridge_invalidates_cached_route(mover,record,dt):
    sim,u=mover
    t=install(sim,{"rivers":[river()],"bridges":[bridge()]})
    # Force the cached-route branch; nearby direct routes intentionally omit a cache.
    t.navigation.config["local_direct_route_m"]=0
    t.movement_target(u,GOAL)
    assert u.metadata.get("_nav_route")
    t.bridges[0]["destroyed"]=True
    t.bridges[0]["integrity"]=0
    run(sim,u,record,dt,reachable=False,reset=False)
