import pytest

from mnsim.application import ApplicationState, SimulationController
from mnsim.model import Side, Unit, UnitType
from mnsim.simulation import Simulation


def _unit(uid="B-1"):
    return Unit(uid=uid, name=uid, side=Side.BLUE, pos=(0.0, 0.0), echelon="PLT", unit_type=UnitType(name="TEST", branch="INFANTRY", max_speed_mps=1.0, detection_range_m=100.0))


def test_controller_controls_do_not_change_engine_semantics():
    sim = Simulation(seed=1)
    ctl = SimulationController(sim)

    ctl.set_speed(32)
    ctl.advance_realtime(0.0625)
    assert abs(sim.time - 2.0) < 1e-9

    ctl.toggle_pause()
    ctl.advance_realtime(1.0)
    assert abs(sim.time - 2.0) < 1e-9


def test_controller_selection_is_id_based_and_safe_across_frontends():
    sim = Simulation(seed=1)
    u = _unit()
    sim.add_unit(u)
    ctl = SimulationController(sim)

    assert ctl.select_unit(u) is u
    assert ctl.state.selected_unit_id == u.uid
    assert ctl.selected_unit is u
    ctl.clear_selection()
    assert ctl.selected_unit is None


def test_view_mode_change_preserves_existing_ui_behavior_of_clearing_selection():
    sim = Simulation(seed=1)
    u = _unit()
    sim.add_unit(u)
    ctl = SimulationController(sim, state=ApplicationState(view_mode="BLUE"))
    ctl.select_unit(u)

    assert ctl.set_view_mode("GOD") == "GOD"
    assert ctl.selected_unit is None
    with pytest.raises(ValueError):
        ctl.set_view_mode("INVALID")


def test_speed_stepping_matches_v41_adjacent_speed_behavior():
    sim = Simulation(seed=1)
    ctl = SimulationController(sim)
    sim.speed = 8.0
    assert ctl.step_speed(1) == 16.0
    assert ctl.step_speed(-1) == 8.0
    sim.speed = 1.0
    assert ctl.step_speed(-1) == 1.0
