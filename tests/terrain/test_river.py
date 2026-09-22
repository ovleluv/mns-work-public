"""강: 도하 능력 없는 유닛의 차단, 가느다란 강의 관통 방지."""
import pytest
from .support import DT, START, GOAL, install, river, run

@pytest.mark.parametrize("dt",DT)
@pytest.mark.parametrize("reverse",[False,True])
@pytest.mark.parametrize("width",[20.0,0.4])
def test_uncrossable_river(mover,record,dt,reverse,width):
    sim,u=mover
    r=river(); r["polygon"]=[[120-width/2,0],[120+width/2,0],[120+width/2,240],[120-width/2,240]]
    t=install(sim,{"rivers":[r]})
    assert not t.passable(u,(120,100))
    a,b=(GOAL,START) if reverse else (START,GOAL)
    def bank_only(p,q):
        assert (p[0]>120+width/2 and q[0]>120+width/2) if reverse else (p[0]<120-width/2 and q[0]<120-width/2)
    run(sim,u,record,dt,start=a,goal=b,reachable=False,check=bank_only)
