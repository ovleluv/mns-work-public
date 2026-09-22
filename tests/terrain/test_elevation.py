"""고도: 완만한 경사 이동, 급경사 경로 차단."""
import pytest
from .support import DT, install, run

@pytest.mark.parametrize("dt",DT)
def test_gentle_elevation_arrival(mover,record,dt):
    sim,u=mover
    install(sim,{"areas":[{"id":"H","type":"ELEVATION","polygon":[[100,0],[240,0],[240,240],[100,240]],"elevation_m":5,"transition_width_m":40}]})
    run(sim,u,record,dt)

def test_steep_grade_is_rejected_by_planner(mover,record):
    sim,u=mover
    t=install(sim,{"areas":[{"id":"H","type":"ELEVATION","polygon":[[100,0],[240,0],[240,240],[100,240]],"elevation_m":100,"transition_width_m":1}]})
    grade=t.slope_angle_deg((99,100),(102,100))
    record.append(dict(unit_type=u.unit_type.name,grade_deg=grade))
    assert abs(grade)>38
    assert not t.navigation.segment_passable(u,(99,100),(102,100))
