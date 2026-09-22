"""평지: 모든 대표 유닛의 정상 이동 대조군."""
import pytest
from .support import DT, install, run

@pytest.mark.parametrize("dt", DT)
def test_open_arrival(mover,record,dt):
    sim,u=mover
    install(sim,{})
    row=run(sim,u,record,dt)
    assert 157<=row["distance_m"]<=160.000001
