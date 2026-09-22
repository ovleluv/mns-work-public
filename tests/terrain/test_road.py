"""도로: 평지 대비 실제 도착 시간, 숲 내부 도로 연속성."""
import pytest
from .support import DT, install, rectangle, run

@pytest.mark.parametrize("dt",DT)
def test_road_travel_time(mover,record,dt):
    sim,u=mover
    install(sim,{})
    baseline=run(sim,u,record,dt)
    install(sim,{"roads":[{"points":[[0,100],[240,100]],"width_m":12}]})
    road=run(sim,u,record,dt)
    assert road["elapsed_s"]<=baseline["elapsed_s"]+dt

@pytest.mark.parametrize("dt",DT)
def test_road_through_impassable_forest(mover,record,dt):
    sim,u=mover
    install(sim,{"areas":[rectangle("FOREST",mobility_overrides={"FOOT":0.45,"TRACKED":0,"WHEELED":0,"WHEELED_TOWED":0},impassable_mobility_classes=["TRACKED","WHEELED","WHEELED_TOWED"])],"roads":[{"points":[[0,100],[240,100]],"width_m":12}]})
    assert sim.terrain.passable(u,(120,100))
    run(sim,u,record,dt)


def test_forest_road_exception_is_limited_to_vehicle_restrictions(mover,record):
    sim,u=mover
    road={"points":[[0,100],[240,100]],"width_m":12}
    point=(120,100)
    t=install(sim,{"roads":[road]})
    baseline=t.speed_factor(u,point)
    mobility=u.unit_type.metadata["mobility_class"]
    vehicle=mobility in {"TRACKED","WHEELED","WHEELED_TOWED"}
    record.append(dict(unit_type=u.unit_type.name,road_speed_factor=baseline))
    # Both supported forms of forest restriction grant the same road exception.
    for restriction in ({"mobility_overrides":{mobility:0}},
                        {"impassable_mobility_classes":[mobility],"movement_factor":0.4}):
        forest=rectangle("FOREST",**restriction)
        t=install(sim,{"roads":[road],"areas":[forest]})
        expected=baseline if vehicle else baseline*restriction.get("movement_factor",0)
        assert t.speed_factor(u,point)==pytest.approx(expected)
        assert not t.passable(u,(120,120)), "off-road restriction must remain"
        # Another overlapping area's penalty still applies.
        t.areas.append(rectangle("WOODS",movement_factor=0.5))
        assert t.speed_factor(u,point)==pytest.approx(expected*0.5)
    # Passable forest slowdown is not a road-cleared vehicle restriction.
    t=install(sim,{"roads":[road],"areas":[rectangle("FOREST",mobility_overrides={mobility:0.4})]})
    assert t.speed_factor(u,point)==pytest.approx(baseline*0.4)
    # No new road exception for other terrain types or generic zero factors.
    for area in (rectangle("WOODS",mobility_overrides={mobility:0}),
                 rectangle("FOREST",movement_factor=0)):
        t=install(sim,{"roads":[road],"areas":[area]})
        assert t.speed_factor(u,point)==0
