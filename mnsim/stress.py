"""Combat stress: suppression and morale/cohesion.

Why this exists
---------------
Without it every engagement runs to annihilation: a formation keeps advancing and firing at full
effect until its last element is gone.  Operational research on ground combat consistently finds
the opposite -- fire mostly *suppresses* (the target goes to ground, fires less and less
accurately, stops moving) and formations stop being effective or break well before they are
destroyed.

Design rules (consistent with the rest of the engine)
-----------------------------------------------------
* Generic, not weapon-specific: every incoming round contributes, weighted by the lethality data
  the weapon already has (``max_effect_count``, heavy/anti-armor calibre).  There is no
  machine-gun-only debuff and no per-weapon suppression flag (see v49.6 in README).
* Perception-neutral: the *target* feels fire that physically arrives; nobody gains knowledge.
* Data-driven: all coefficients live in ``combat.stress_model`` (config/defaults.json) and a
  unit type may set ``metadata.morale`` (training/quality baseline, default 1.0).
* Can be switched off with ``stress_model.enabled = false`` for calibration comparisons.

State lives on ``Unit.suppression`` (0..1) and ``Unit.morale`` (0..1).
"""
from __future__ import annotations

import math
from typing import Any, Dict

from .model import Unit, UnitState

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    # --- suppression inputs ---
    "direct_suppression_per_round": 0.035,
    "direct_hit_multiplier": 1.5,
    "heavy_round_multiplier": 1.5,          # anti-armor / >=20 mm rounds landing near infantry
    "indirect_suppression_per_impact": 0.30,
    "indirect_effect_radius_factor": 1.6,   # impacts within 1.6x the footprint radius count
    "loss_suppression_per_strength": 0.8,   # suppression added per fraction of strength lost
    "buttoned_up_factor": 0.30,             # formations with no exposed personnel
    "reference_formation_size": 10.0,       # a squad-sized formation feels a round at full weight
    "suppression_half_life_s": 15.0,
    # --- suppression effects ---
    "rate_of_fire_penalty": 1.2,            # cycle interval x (1 + k*S)
    "accuracy_penalty": 0.55,               # hit probability x (1 - k*S)
    "movement_penalty": 0.75,               # advance speed x (1 - k*S)
    "retreat_movement_penalty": 0.35,
    "detection_penalty": 0.5,
    "suppressed_threshold": 0.5,
    "pinned_threshold": 0.8,
    # --- morale ---  (defaults put the typical break point at roughly 30-50 % strength loss,
    #                  earlier under sustained suppression or after losing the leadership element)
    "morale_loss_per_strength": 1.5,        # morale lost per fraction of initial strength lost
    "leader_loss_morale": 0.10,
    "sustained_suppression_morale_per_s": 0.004,
    "morale_recovery_per_s": 0.003,
    "morale_recovery_max_suppression": 0.2,
    "morale_ceiling_loss_factor": 1.0,      # recovery ceiling = baseline * (1 - k * loss_ratio)
    "shaken_threshold": 0.50,
    "broken_threshold": 0.30,
    "hold_at_all_costs_broken_threshold": 0.08,
    "rally_threshold": 0.55,
    "shaken_accuracy_factor": 0.85,
    "broken_rate_of_fire_factor": 0.33,
    "broken_withdraw_m": 400.0,
}

LEADER_ROLES = ("HQ", "LEADER", "COMMAND", "PLT_HQ", "COY_HQ", "BN_HQ")


class CombatStressModel:
    def __init__(self, sim):
        self.sim = sim

    # ------------------------------------------------------------------ config / state helpers
    def cfg(self) -> Dict[str, Any]:
        out = dict(DEFAULTS)
        out.update(dict(self.sim.combat_config.get("stress_model", {}) or {}))
        return out

    @property
    def enabled(self) -> bool:
        return bool(self.cfg().get("enabled", True))

    @staticmethod
    def baseline_morale(unit: Unit) -> float:
        raw = unit.metadata.get("morale", unit.unit_type.metadata.get("morale", 1.0))
        try:
            return max(0.05, min(1.0, float(raw)))
        except (TypeError, ValueError):
            return 1.0

    def _ensure(self, unit: Unit) -> None:
        if "_morale_initialized" not in unit.metadata:
            unit.metadata["_morale_initialized"] = True
            unit.morale = self.baseline_morale(unit)

    @staticmethod
    def _exposed_personnel(unit: Unit) -> int:
        return sum(e.count for e in unit.elements.values()
                   if e.category.upper() == "PERSONNEL" and e.count > 0 and unit.element_exposed(e))

    def _size_weight(self, unit: Unit) -> float:
        ref = max(1.0, float(self.cfg()["reference_formation_size"]))
        size = max(1.0, self._exposed_personnel(unit) + 4.0 * unit.equipment)
        return math.sqrt(size / ref) if size > ref else 1.0

    def _posture_weight(self, unit: Unit) -> float:
        # Prepared positions shield from suppression as they do from effects.
        return 1.0 - 0.4 * self.sim.dig_in_fraction(unit)

    def _add_suppression(self, unit: Unit, amount: float, cause: str) -> None:
        if amount <= 0.0 or not unit.alive:
            return
        self._ensure(unit)
        c = self.cfg()
        before = unit.suppression
        unit.suppression = min(1.0, unit.suppression + amount)
        for name, thr in (("SUPPRESSED", c["suppressed_threshold"]), ("PINNED", c["pinned_threshold"])):
            if before < thr <= unit.suppression:
                self.sim.log(name, unit=unit.uid, suppression=round(unit.suppression, 2), cause=cause)

    # ------------------------------------------------------------------ inputs
    def on_direct_fire(self, target: Unit, shooter: Unit, weapon, distance_m: float, hit: bool) -> None:
        if not self.enabled:
            return
        c = self.cfg()
        weight = math.sqrt(max(1, int(getattr(weapon, "max_effect_count", 1) or 1)))
        cap = str(getattr(weapon, "capability", "")).upper()
        heavy = cap in ("ANTI_ARMOR", "DIRECT_FIRE_HEAVY") or float(weapon.metadata.get("caliber_mm", 0.0)) >= 20.0
        if heavy:
            weight *= float(c["heavy_round_multiplier"])
        if hit:
            weight *= float(c["direct_hit_multiplier"])
        rng = max(1.0, float(getattr(weapon, "range_m", 1.0)))
        range_w = 0.5 + 0.5 * max(0.0, 1.0 - float(distance_m) / rng)
        amount = float(c["direct_suppression_per_round"]) * weight * range_w * self._posture_weight(target)
        if self._exposed_personnel(target) == 0:
            amount *= float(c["buttoned_up_factor"])
        self._add_suppression(target, amount / self._size_weight(target), "DIRECT_FIRE")

    def on_indirect_impact(self, victim: Unit, normalized_radius: float) -> None:
        """``normalized_radius``: impact distance in footprint radii (<=1 inside the footprint)."""
        if not self.enabled:
            return
        c = self.cfg()
        reach = max(1.0, float(c["indirect_effect_radius_factor"]))
        if normalized_radius > reach:
            return
        proximity = 1.0 - max(0.0, float(normalized_radius)) / reach
        amount = float(c["indirect_suppression_per_impact"]) * (0.3 + 0.7 * proximity) * self._posture_weight(victim)
        if self._exposed_personnel(victim) == 0:
            amount *= float(c["buttoned_up_factor"])
        # Area fire suppresses a whole formation more evenly than aimed fire: softer size scaling.
        self._add_suppression(victim, amount / math.sqrt(self._size_weight(victim)), "INDIRECT_FIRE")

    def on_losses(self, unit: Unit, strength_fraction_lost: float, element=None) -> None:
        if not self.enabled or strength_fraction_lost <= 0.0:
            return
        self._ensure(unit)
        c = self.cfg()
        self._add_suppression(unit, float(c["loss_suppression_per_strength"]) * strength_fraction_lost, "CASUALTIES")
        hit = float(c["morale_loss_per_strength"]) * strength_fraction_lost
        if element is not None and any(r in str(getattr(element, "role", "")).upper() for r in LEADER_ROLES):
            hit += float(c["leader_loss_morale"])
        self._change_morale(unit, -hit, "CASUALTIES")

    # ------------------------------------------------------------------ time evolution
    def update(self, dt: float) -> None:
        if not self.enabled or dt <= 0.0:
            return
        c = self.cfg()
        decay = 0.5 ** (dt / max(0.1, float(c["suppression_half_life_s"])))
        for u in self.sim.units.values():
            if not u.alive:
                continue
            self._ensure(u)
            if u.suppression > 0.0:
                u.suppression *= decay
                if u.suppression < 1e-4:
                    u.suppression = 0.0
            s = u.suppression
            if s > float(c["suppressed_threshold"]):
                self._change_morale(u, -float(c["sustained_suppression_morale_per_s"]) * (s - float(c["suppressed_threshold"])) * dt,
                                    "SUSTAINED_FIRE")
            elif s <= float(c["morale_recovery_max_suppression"]):
                ceiling = self.baseline_morale(u) * (1.0 - float(c["morale_ceiling_loss_factor"]) * u.loss_ratio)
                if u.morale < ceiling:
                    self._change_morale(u, min(ceiling - u.morale, float(c["morale_recovery_per_s"]) * dt), "RECOVERY")

    def _broken_threshold(self, unit: Unit) -> float:
        c = self.cfg()
        if bool(self.sim.doctrine.directive(unit, "hold_at_all_costs", False)):
            return float(c["hold_at_all_costs_broken_threshold"])
        return float(c["broken_threshold"])

    def _change_morale(self, unit: Unit, delta: float, cause: str) -> None:
        if delta == 0.0:
            return
        before_state = self.morale_state(unit)
        unit.morale = max(0.0, min(1.0, unit.morale + delta))
        after_state = self.morale_state(unit)
        if after_state != before_state:
            unit.metadata["morale_state"] = after_state
            if before_state == "BROKEN":
                unit.metadata.pop("_broken_withdraw_dest", None)
                if str(unit.metadata.get("tactical_reason", "")).startswith("MORALE BROKEN"):
                    unit.metadata.pop("tactical_reason", None)
            self.sim.log("MORALE_" + after_state, unit=unit.uid, morale=round(unit.morale, 3),
                         suppression=round(unit.suppression, 2), cause=cause, previous=before_state)

    # ------------------------------------------------------------------ queries used by the engine
    def morale_state(self, unit: Unit) -> str:
        if not self.enabled:
            return "STEADY"
        c = self.cfg()
        prev = str(unit.metadata.get("morale_state", "STEADY"))
        m = unit.morale
        if m < self._broken_threshold(unit):
            return "BROKEN"
        if prev == "BROKEN" and m < float(c["rally_threshold"]):
            return "BROKEN"          # hysteresis: a broken formation must rally, not just tick up
        if m < float(c["shaken_threshold"]):
            return "SHAKEN"
        return "STEADY"

    def pinned(self, unit: Unit) -> bool:
        return self.enabled and unit.suppression >= float(self.cfg()["pinned_threshold"])

    def fire_interval_factor(self, unit: Unit) -> float:
        if not self.enabled:
            return 1.0
        c = self.cfg()
        f = 1.0 + float(c["rate_of_fire_penalty"]) * unit.suppression
        if self.morale_state(unit) == "BROKEN":
            f /= max(0.05, float(c["broken_rate_of_fire_factor"]))
        return f

    def accuracy_factor(self, unit: Unit) -> float:
        if not self.enabled:
            return 1.0
        c = self.cfg()
        f = max(0.05, 1.0 - float(c["accuracy_penalty"]) * unit.suppression)
        if self.morale_state(unit) != "STEADY":
            f *= float(c["shaken_accuracy_factor"])
        return f

    def movement_factor(self, unit: Unit) -> float:
        if not self.enabled:
            return 1.0
        c = self.cfg()
        key = "retreat_movement_penalty" if unit.state == UnitState.RETREATING else "movement_penalty"
        return max(0.2, 1.0 - float(c[key]) * unit.suppression)

    def detection_factor(self, unit: Unit) -> float:
        if not self.enabled:
            return 1.0
        return max(0.1, 1.0 - float(self.cfg()["detection_penalty"]) * unit.suppression)
