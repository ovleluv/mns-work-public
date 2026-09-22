import math
from mnsim.model import Unit, UnitType, Side
from mnsim.simulation import Simulation


def _unit(uid, branch, pos=(0.0,0.0), watch=0.0):
    typ=UnitType(name=uid+'_TYPE', branch=branch, max_speed_mps=4.0, detection_range_m=1000.0)
    return Unit(uid=uid,name=uid,side=Side.BLUE,echelon='PLT',unit_type=typ,pos=pos,watch_heading_deg=watch)


def test_watch_slew_uses_shortest_clockwise_path():
    sim=Simulation(seed=1)
    u=_unit('U','INFANTRY',watch=350.0); sim.add_unit(u)
    u.current_order=None
    # Explicitly drive desired orientation through a current-order-like metadata-free target track is overkill;
    # monkeypatch only the desired-angle policy while retaining the real slew integrator.
    sim._desired_watch_heading=lambda unit: 10.0
    sim._update_watch_heading(u,0.25)
    assert math.isclose(u.watch_heading_deg,1.25,abs_tol=1e-6)
    assert abs(sim._angle_delta_deg(10.0,u.watch_heading_deg)) < 20.0


def test_infantry_slews_faster_than_armor():
    sim=Simulation(seed=2)
    inf=_unit('I','INFANTRY',watch=0.0); arm=_unit('A','ARMOR',watch=0.0)
    sim.add_unit(inf); sim.add_unit(arm)
    sim._desired_watch_heading=lambda unit: 90.0
    sim._update_watch_heading(inf,1.0); sim._update_watch_heading(arm,1.0)
    assert inf.watch_heading_deg > arm.watch_heading_deg
    assert math.isclose(inf.watch_heading_deg,45.0,abs_tol=1e-6)
    assert math.isclose(arm.watch_heading_deg,20.0,abs_tol=1e-6)


def test_180_degree_turn_takes_multiple_seconds():
    sim=Simulation(seed=3)
    inf=_unit('I','INFANTRY',watch=0.0); arm=_unit('A','ARMOR',watch=0.0)
    sim.add_unit(inf); sim.add_unit(arm)
    sim._desired_watch_heading=lambda unit: 180.0
    for _ in range(3):
        sim._update_watch_heading(inf,1.0); sim._update_watch_heading(arm,1.0)
    assert abs(sim._angle_delta_deg(180.0,inf.watch_heading_deg)) > 1.0
    assert abs(sim._angle_delta_deg(180.0,arm.watch_heading_deg)) > abs(sim._angle_delta_deg(180.0,inf.watch_heading_deg))
    sim._update_watch_heading(inf,1.0)
    assert abs(sim._angle_delta_deg(180.0,inf.watch_heading_deg)) < 1e-6
