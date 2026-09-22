"""숲: 보병 통과·감속, 차량 우회 및 도착."""
import pytest
from .support import DT, install, rectangle, outside_rectangle, run

@pytest.mark.parametrize("dt",DT)
def test_forest_traversal_or_detour(mover,record,dt):
    sim,u=mover
    t=install(sim,{"areas":[rectangle("FOREST",mobility_overrides={"FOOT":0.45,"TRACKED":0,"WHEELED":0,"WHEELED_TOWED":0},impassable_mobility_classes=["TRACKED","WHEELED","WHEELED_TOWED"])]})
    foot=u.unit_type.metadata["mobility_class"]=="FOOT"
    assert t.passable(u,(120,100)) is foot
    if foot: assert 0<t.speed_factor(u,(120,100))<t.speed_factor(u,(40,100))
    row=run(sim,u,record,dt,check=None if foot else outside_rectangle)
    if not foot: assert row["distance_m"]>160
