"""건물: 일반 이동 명령의 건물 관통 방지와 우회 도착."""
import pytest
from .support import DT, install, rectangle, outside_rectangle, run

@pytest.mark.parametrize("dt",DT)
def test_building_detour(mover,record,dt):
    sim,u=mover
    t=install(sim,{"areas":[rectangle("BUILDING")]})
    assert not t.passable(u,(120,100))
    run(sim,u,record,dt,check=outside_rectangle)
