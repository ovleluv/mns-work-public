"""Shared final movement speed, in metres per second.

Apply echelon loss AFTER state, terrain, equipment and grade multipliers.
The 10% floor preserves positive low-speed movement without reviving a stop.
"""
ECHELON_PENALTY_KPH = {"PLT": 0.0, "COY": 0.5, "BN": 1.0}
LOW_SPEED_RETAINED_FRACTION = 0.10


def adjusted_speed_mps(base_speed_mps, state_factor, terrain_factor, echelon):
    raw = max(0.0, float(base_speed_mps) * float(state_factor) * float(terrain_factor))
    penalty = ECHELON_PENALTY_KPH.get(str(echelon).upper(), 0.0) / 3.6
    return max(raw * LOW_SPEED_RETAINED_FRACTION, raw - penalty)


def movement_speed_mps(unit, terrain=None, pos=None, dest=None, *, state=None):
    """Local speed; callers enforce segment passability and remaining distance.

    Planning may explicitly use MOVING while an idle unit receives a move order.
    Echelon comes from the live unit, not inherited unit-type metadata.
    """
    if unit.crew_failure_reason:
        return 0.0
    state = unit.state if state is None else state
    name = getattr(state, "value", state)
    factors = unit.unit_type.metadata.get("state_speed_factors", {})
    state_factor = float(factors.get(name, 0.7 if name == "RETREATING" else 1.0))
    pos = unit.pos if pos is None else pos
    terrain_factor = terrain.speed_factor(unit, pos, dest) if terrain is not None else 1.0
    return adjusted_speed_mps(unit.unit_type.max_speed_mps, state_factor,
                              terrain_factor, unit.echelon)
