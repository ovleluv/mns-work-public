"""도하: 명시적 보병 도하 허용/차량 차단과 능력 부여 대조군."""
import copy
import pytest
from .support import DT, install, river, run

@pytest.mark.parametrize("dt",DT)
def test_authored_foot_ford(mover,record,dt):
    sim,u=mover
    install(sim,{"rivers":[river(mobility_overrides={"FOOT":0.35,"TRACKED":0,"WHEELED":0,"WHEELED_TOWED":0})]})
    foot=u.unit_type.metadata["mobility_class"]=="FOOT"
    assert sim.terrain.passable(u,(120,100)) is foot
    run(sim,u,record,dt,reachable=foot)

@pytest.mark.parametrize("dt",DT)
def test_explicit_water_crossing_capability(mover,record,dt):
    sim,u=mover
    # Synthetic capability control; does not claim native amphibious capability.
    u.unit_type=copy.deepcopy(u.unit_type)
    u.unit_type.metadata["mobility_capabilities"]=["WATER_CROSSING"]
    install(sim,{"rivers":[river(movement_factor=0.35)]})
    run(sim,u,record,dt)
