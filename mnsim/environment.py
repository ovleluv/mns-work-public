from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class ObservationModifiers:
    """Multipliers applied to a formation-level observation profile.

    The engine deliberately separates *sensor baseline* from *environmental degradation*.
    Defaults are neutral (1.0), so scenarios without weather/vegetation/buildings behave as
    clear-day open terrain.  Future terrain/weather modules can provide the same modifier
    contract without changing the sensor/detection code.
    """
    range_factor: float = 1.0
    fov_factor: float = 1.0
    awareness_factor: float = 1.0
    detection_factor: float = 1.0

    def combine(self, other: "ObservationModifiers") -> "ObservationModifiers":
        return ObservationModifiers(
            self.range_factor * other.range_factor,
            self.fov_factor * other.fov_factor,
            self.awareness_factor * other.awareness_factor,
            self.detection_factor * other.detection_factor,
        )


class EnvironmentObservationModel:
    """Data-driven observation degradation layer.

    This is intentionally not a LOS/elevation engine yet.  It supplies a stable interface for
    vegetation, urban clutter, precipitation, fog, smoke and illumination.  A future raster/LOS
    implementation only needs to return the same four multipliers.
    """

    def __init__(self, combat_config: Dict[str, Any] | None = None):
        self.config = combat_config if combat_config is not None else {}

    @staticmethod
    def _mods(raw: Dict[str, Any] | None) -> ObservationModifiers:
        raw = raw or {}
        return ObservationModifiers(
            max(0.0, float(raw.get("range_factor", 1.0))),
            max(0.05, float(raw.get("fov_factor", 1.0))),
            max(0.0, float(raw.get("awareness_factor", 1.0))),
            max(0.0, float(raw.get("detection_factor", 1.0))),
        )

    def weather_modifier(self, sensor_mode: str = "VISUAL") -> ObservationModifiers:
        env = dict(self.config.get("environment", {}))
        weather = str(env.get("weather", "CLEAR")).upper()
        table = dict(env.get("weather_observation_modifiers", {}))
        raw = dict(table.get(weather, table.get("CLEAR", {})))
        # Optional sensor-specific override, e.g. THERMAL can degrade less in darkness/haze.
        sensor_overrides = dict(raw.get("sensor_overrides", {}))
        if sensor_mode.upper() in sensor_overrides:
            merged = dict(raw)
            merged.update(dict(sensor_overrides[sensor_mode.upper()]))
            raw = merged
        return self._mods(raw)

    def illumination_modifier(self, sensor_mode: str = "VISUAL") -> ObservationModifiers:
        env = dict(self.config.get("environment", {}))
        illumination = str(env.get("illumination", "DAY")).upper()
        table = dict(env.get("illumination_observation_modifiers", {}))
        raw = dict(table.get(illumination, table.get("DAY", {})))
        sensor_overrides = dict(raw.get("sensor_overrides", {}))
        if sensor_mode.upper() in sensor_overrides:
            merged = dict(raw)
            merged.update(dict(sensor_overrides[sensor_mode.upper()]))
            raw = merged
        return self._mods(raw)

    def modifier(self, terrain, observer, target=None, sensor_mode: str = "VISUAL") -> ObservationModifiers:
        result = self.weather_modifier(sensor_mode).combine(self.illumination_modifier(sensor_mode))
        if terrain is not None and hasattr(terrain, "observation_modifier"):
            raw = terrain.observation_modifier(observer.pos, target.pos if target is not None else observer.pos,
                                               sensor_mode=sensor_mode)
            result = result.combine(self._mods(raw))

            # Vegetation concealment is strongest for small, stationary infantry targets.
            # This changes detectability only; it does not grant artificial damage resistance.
            branches = {str(x).upper() for x in self.config.get(
                "vegetation_concealment_branches", ("INFANTRY", "MECH_INFANTRY", "MOTORIZED_INFANTRY"))}
            if target is not None and str(getattr(target, "branch", "")).upper() in branches:
                areas = terrain.area_at(target.pos) if hasattr(terrain, "area_at") else []
                vegetation = {str(a.get("type", "")).upper() for a in areas}
                state = str(getattr(getattr(target, "state", None), "name", getattr(target, "state", ""))).upper()
                if "FOREST" in vegetation:
                    posture_factor = 0.55 if state == "DEFENDING" else (0.68 if state not in ("MOVING", "ATTACKING", "RETREATING") else 0.88)
                    result = result.combine(ObservationModifiers(detection_factor=posture_factor))
                elif "WOODS" in vegetation:
                    posture_factor = 0.68 if state == "DEFENDING" else (0.78 if state not in ("MOVING", "ATTACKING", "RETREATING") else 0.92)
                    result = result.combine(ObservationModifiers(detection_factor=posture_factor))
        return result
