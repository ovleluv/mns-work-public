"""호수: 보병 수영과 차량 우회."""
import pytest
from .support import DT, install, rectangle, outside_rectangle, run

@pytest.mark.parametrize("dt",DT)
def test_lake_swim_or_detour(mover,record,dt):
    sim,u=mover
    t=install(sim,{"areas":[rectangle("LAKE",mobility_overrides={"FOOT":0.16,"TRACKED":0,"WHEELED":0,"WHEELED_TOWED":0})]})
    foot=u.unit_type.metadata["mobility_class"]=="FOOT"
    assert t.passable(u,(120,100)) is foot
    run(sim,u,record,dt,check=None if foot else outside_rectangle)
