"""성긴 숲: 통행 가능한 감속 지형의 실제 이동 비교."""
import pytest
from .support import DT, install, run

@pytest.mark.parametrize("dt",DT)
def test_woods_slows_without_stalling(mover,record,dt):
    sim,u=mover
    install(sim,{})
    baseline=run(sim,u,record,dt)
    install(sim,{"areas":[{"id":"W","type":"WOODS","polygon":[[0,0],[240,0],[240,240],[0,240]],"movement_factor":0.5}]})
    woods=run(sim,u,record,dt)
    assert woods["elapsed_s"]>baseline["elapsed_s"]
