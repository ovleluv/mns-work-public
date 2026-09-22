"""UI-neutral application/session control for the MNS simulator.

This module is intentionally free of Pygame/Qt/Tk dependencies.  GUI frontends should
translate framework-specific input events into these small control operations, while the
simulation kernel remains in :mod:`mnsim.simulation`.

The class is deliberately thin: it does not implement combat, doctrine, sensing,
movement, or rendering policy.  It only owns presentation/session state that otherwise
would be duplicated by every frontend.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from .model import Side, Unit
from .simulation import Simulation


DEFAULT_SIM_SPEEDS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0)
VALID_VIEW_MODES = (Side.BLUE.value, Side.RED.value, "GOD")


@dataclass
class ApplicationState:
    """Frontend-neutral, non-simulation state.

    None of these fields changes simulation truth.  A future PySide6, web, or replay
    frontend can maintain the same semantics without importing Pygame.
    """

    view_mode: str = Side.BLUE.value
    selected_unit_id: Optional[str] = None
    help_visible: bool = False


class SimulationController:
    """Thin application facade over a :class:`Simulation` instance.

    It centralizes generic controls (pause, speed, view side, selection, wall-clock
    advancement) but intentionally exposes ``sim`` for existing renderers.  This keeps
    the v41 Pygame behavior unchanged while establishing a stable migration seam.
    """

    def __init__(
        self,
        sim: Simulation,
        *,
        state: Optional[ApplicationState] = None,
        supported_speeds: Sequence[float] = DEFAULT_SIM_SPEEDS,
    ):
        self.sim = sim
        self.state = state or ApplicationState()
        speeds = tuple(float(v) for v in supported_speeds)
        if not speeds:
            raise ValueError("supported_speeds must not be empty")
        self.supported_speeds = speeds

    @property
    def selected_unit(self) -> Optional[Unit]:
        uid = self.state.selected_unit_id
        if uid is None:
            return None
        return self.sim.units.get(uid)

    def select_unit(self, unit_or_uid: Unit | str | None) -> Optional[Unit]:
        if unit_or_uid is None:
            self.state.selected_unit_id = None
            return None
        uid = unit_or_uid.uid if isinstance(unit_or_uid, Unit) else str(unit_or_uid)
        if uid not in self.sim.units:
            self.state.selected_unit_id = None
            return None
        self.state.selected_unit_id = uid
        return self.sim.units[uid]

    def clear_selection(self) -> None:
        self.state.selected_unit_id = None

    def set_view_mode(self, mode: str) -> str:
        mode = str(mode).upper()
        if mode not in VALID_VIEW_MODES:
            raise ValueError(f"unsupported view mode: {mode}")
        self.state.view_mode = mode
        self.clear_selection()
        return mode

    def toggle_pause(self) -> bool:
        self.sim.paused = not self.sim.paused
        return self.sim.paused

    def set_speed(self, speed: float) -> float:
        speed = float(speed)
        if speed not in self.supported_speeds:
            raise ValueError(f"unsupported simulation speed: {speed:g}x")
        self.sim.speed = speed
        return self.sim.speed

    def step_speed(self, direction: int) -> float:
        current = min(
            range(len(self.supported_speeds)),
            key=lambda i: abs(self.supported_speeds[i] - float(self.sim.speed)),
        )
        current = max(0, min(len(self.supported_speeds) - 1, current + int(direction)))
        self.sim.speed = self.supported_speeds[current]
        return self.sim.speed

    def advance_realtime(self, wall_dt: float, max_sim_step_s: float = 0.25) -> None:
        """Advance only simulation time; a frontend decides whether modal UI should call it."""
        self.sim.advance_realtime(wall_dt, max_sim_step_s=max_sim_step_s)
