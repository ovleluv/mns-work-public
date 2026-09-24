"""Fire and movement: support by fire, assault and close combat.

Why this exists
---------------
Doctrine otherwise stops an attacking formation at its (STANDOFF) engagement range and leaves it
there, so an attack never closes and a defender is never ejected from its position.  Real attacks
fix the enemy with fire, close once the enemy's fire slackens, and decide the position in close
combat -- usually by the defender giving way rather than by being killed to the last man.

Model
-----
* SUPPORT BY FIRE: an attacking formation (ATTACK / ATTACK_UNIT / DESTROY_UNIT) that has an
  actionable contact inside its stand-off range engages from there, as before.
* ASSAULT: after ``support_by_fire_s`` of engagement, if the attacker is steady and not heavily
  suppressed, has fire superiority (it fired ``fire_superiority_ratio`` times as many rounds as it
  received over ``fire_window_s``, or has not been fired on for ``quiet_s``) -- or has supported by
  fire for ``commit_after_s`` (at stand-off only a few long-range weapons reach, so fire volume is
  a poor measure of superiority) -- and has taken almost no losses in the last
  ``recent_loss_window_s``, the formation closes on the contact's perceived position at full
  movement speed.  All of these are the attacker's own experience.  The
  doctrine layer reads :meth:`AssaultModel.desired_range` so no order code changes.
* CLOSE COMBAT: while an assaulting formation is within ``contact_m`` of its target, each
  ``close_combat_interval_s`` resolves a short, physical exchange: casualties on both sides and a
  morale shock driven by the fighting-power ratio (exposed troops x (1 - suppression) x morale,
  defender weighted by its prepared position).  Defenders usually break and fall back (stress
  model) -- the position is taken -- rather than being annihilated.
* An assault fails and reverts to support by fire if the attacker becomes pinned or shaken or
  loses ``abort_loss_fraction`` of its strength during the assault; it may be retried after
  ``retry_cooldown_s``.

Close-combat resolution uses physical truth (who is actually there) the same way damage
resolution does; the *decision* to assault uses only the attacker's own Track and its own
experience of incoming fire.  Everything is configurable under ``combat.assault``;
``enabled: false`` restores stand-off-only attacks.
"""
from __future__ import annotations

import math
from typing import Any, Dict

from .model import Unit, UnitState

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "support_by_fire_s": 60.0,
    "quiet_s": 20.0,                  # longer than a rifle engagement cycle: fire really slackened
    "fire_window_s": 60.0,
    "fire_superiority_ratio": 2.0,    # own rounds fired / rounds received over fire_window_s
    "commit_after_s": 180.0,          # a steady attacker not taking losses commits after this long
    "recent_loss_window_s": 60.0,
    "max_recent_loss_fraction": 0.05, # do not assault while still taking losses
    "abort_loss_fraction": 0.15,      # call the assault off after losing this much of initial strength
    "max_attacker_suppression": 0.4,
    "abort_suppression": 0.65,
    "retry_cooldown_s": 90.0,
    "contact_m": 40.0,
    "close_combat_interval_s": 10.0,
    "casualty_fraction_per_round": 0.05,
    "morale_shock_per_round": 0.10,
    "prepared_position_bonus": 1.0,   # fully dug-in defender fights with (1 + bonus) x power
    "vehicle_power": 3.0,             # close-combat weight of one surviving vehicle
}

ASSAULT_ORDERS = ("ATTACK", "ATTACK_UNIT", "DESTROY_UNIT")


class AssaultModel:
    def __init__(self, sim):
        self.sim = sim

    # ------------------------------------------------------------------ config / state
    def cfg(self) -> Dict[str, Any]:
        out = dict(DEFAULTS)
        out.update(dict(self.sim.combat_config.get("assault", {}) or {}))
        return out

    @property
    def enabled(self) -> bool:
        return bool(self.cfg().get("enabled", True))

    @staticmethod
    def state(unit: Unit) -> dict | None:
        st = unit.metadata.get("_assault")
        return st if isinstance(st, dict) else None

    def assaulting(self, unit: Unit, target_uid: str | None = None) -> bool:
        st = self.state(unit)
        return bool(st and st.get("phase") == "ASSAULT" and (target_uid is None or st.get("target") == target_uid))

    def desired_range(self, unit: Unit, target: Unit) -> float | None:
        """Engagement distance while assaulting ``target``; ``None`` = normal doctrine."""
        if self.enabled and self.assaulting(unit, target.uid):
            return float(self.cfg()["contact_m"])
        return None

    # ------------------------------------------------------------------ helpers
    def _assault_allowed(self, unit: Unit) -> bool:
        o = unit.current_order
        if o is None or str(o.kind).upper() not in ASSAULT_ORDERS:
            return False
        return bool(self.sim.doctrine.directive(unit, "assault", True))

    def _steady(self, unit: Unit, max_suppression: float) -> bool:
        stress = getattr(self.sim, "stress", None)
        if stress is None or not stress.enabled:
            return True
        return stress.morale_state(unit) == "STEADY" and unit.suppression <= max_suppression

    def _strength_history(self, unit: Unit, now: float, window: float):
        hist = unit.metadata.setdefault("_assault_strength_hist", [])
        if not hist or now - hist[-1][0] >= 2.0:
            hist.append((now, unit.current_strength))
        while len(hist) > 2 and now - hist[1][0] > window:
            hist.pop(0)
        return hist

    def _recent_loss_fraction(self, unit: Unit, now: float, window: float) -> float:
        hist = self._strength_history(unit, now, window)
        init = max(1e-9, unit.initial_strength)
        return max(0.0, (hist[0][1] - unit.current_strength) / init)

    def record_fire(self, shooter: Unit, target: Unit) -> None:
        """Remember rounds fired/received (both sides can count what they themselves experience)."""
        now = self.sim.time
        window = float(self.cfg()["fire_window_s"])
        for unit, key in ((shooter, "_rounds_out"), (target, "_rounds_in")):
            times = unit.metadata.setdefault(key, [])
            times.append(now)
            while times and now - times[0] > window:
                times.pop(0)

    def _fire_superiority(self, unit: Unit, now: float) -> bool:
        c = self.cfg()
        window = float(c["fire_window_s"])
        out = sum(1 for t in unit.metadata.get("_rounds_out", []) if now - t <= window)
        inc = sum(1 for t in unit.metadata.get("_rounds_in", []) if now - t <= window)
        quiet = now - float(unit.metadata.get("_last_incoming_fire_t", -1e9)) >= float(c["quiet_s"])
        return quiet or (out > 0 and out >= float(c["fire_superiority_ratio"]) * inc)

    def _primary_contact(self, unit: Unit):
        tid = unit.target_id
        if tid:
            tgt = self.sim.units.get(tid)
            tr = unit.local_tracks.get(tid)
            if tgt is not None and tr is not None and self.sim._track_for(unit, tgt) is not None:
                return tgt, tr
        return None

    @staticmethod
    def _exposed_personnel(unit: Unit) -> int:
        return sum(e.count for e in unit.elements.values()
                   if e.category.upper() == "PERSONNEL" and e.count > 0 and unit.element_exposed(e))

    def _power(self, unit: Unit, defending: bool) -> float:
        c = self.cfg()
        stress = getattr(self.sim, "stress", None)
        s = unit.suppression if stress is not None and stress.enabled else 0.0
        morale = unit.morale if stress is not None and stress.enabled else 1.0
        bodies = self._exposed_personnel(unit) + float(c["vehicle_power"]) * sum(
            e.fire_capable_item_count for e in unit.elements.values() if e.category.upper() == "EQUIPMENT")
        power = bodies * max(0.05, 1.0 - 0.7 * s) * max(0.1, morale)
        if defending:
            power *= 1.0 + float(c["prepared_position_bonus"]) * self.sim.dig_in_fraction(unit)
        return power

    # ------------------------------------------------------------------ per step
    def update(self, dt: float) -> None:
        if not self.enabled:
            return
        c = self.cfg()
        now = self.sim.time
        for u in list(self.sim.units.values()):
            if not u.alive:
                continue
            st = self.state(u)
            if not self._assault_allowed(u):
                if st:
                    u.metadata.pop("_assault", None)
                continue
            if st is None or st.get("phase") == "SUPPORT":
                self._strength_history(u, now, float(c["recent_loss_window_s"]))
            contact = self._primary_contact(u)
            if contact is None:
                if st and st.get("phase") == "ASSAULT":
                    self.sim.log("ASSAULT_END", unit=u.uid, target=st.get("target"), reason="NO_CONTACT")
                u.metadata.pop("_assault", None)
                continue
            tgt, tr = contact
            if st is None or st.get("target") != tgt.uid:
                st = {"target": tgt.uid, "phase": "SUPPORT", "since": None, "retry_after": -1e9}
                u.metadata["_assault"] = st
            if st["phase"] == "SUPPORT":
                # Support by fire: only counts while actually engaging from the stand-off line.
                if u.state == UnitState.ENGAGING:
                    st["since"] = now if st["since"] is None else st["since"]
                else:
                    st["since"] = None
                superior = (self._fire_superiority(u, now)
                            or (st["since"] is not None and now - st["since"] >= float(c["commit_after_s"])))
                recent = self._recent_loss_fraction(u, now, float(c["recent_loss_window_s"]))
                if (st["since"] is not None and now - st["since"] >= float(c["support_by_fire_s"])
                        and now >= float(st.get("retry_after", -1e9)) and superior
                        and recent <= float(c["max_recent_loss_fraction"])
                        and self._steady(u, float(c["max_attacker_suppression"]))):
                    st["phase"] = "ASSAULT"; st["started"] = now; st["next_round"] = now
                    st["start_strength"] = u.current_strength
                    self.sim.log("ASSAULT_START", unit=u.uid, target=tgt.uid,
                                 perceived_distance=round(math.dist(u.pos, tr.estimated_pos), 1))
                continue
            # ASSAULT phase
            self._strength_history(u, now, float(c["recent_loss_window_s"]))
            lost = (float(st.get("start_strength", u.current_strength)) - u.current_strength) / max(1e-9, u.initial_strength)
            if not self._steady(u, float(c["abort_suppression"])) or lost >= float(c["abort_loss_fraction"]):
                st["phase"] = "SUPPORT"; st["since"] = None
                st["retry_after"] = now + float(c["retry_cooldown_s"])
                self.sim.log("ASSAULT_FAILED", unit=u.uid, target=tgt.uid,
                             suppression=round(u.suppression, 2), morale=round(u.morale, 2))
                continue
            if math.dist(u.pos, tgt.pos) <= float(c["contact_m"]) and now >= float(st.get("next_round", now)):
                st["next_round"] = now + float(c["close_combat_interval_s"])
                self._close_combat_round(u, tgt)

    def _close_combat_round(self, attacker: Unit, defender: Unit) -> None:
        c = self.cfg()
        rng = self.sim.rng
        pa = self._power(attacker, defending=False)
        pd = self._power(defender, defending=True)
        if pa <= 0.0 or pd <= 0.0:
            return
        ratio = pa / pd
        base = float(c["casualty_fraction_per_round"])
        losses = {}
        for victim, frac, source in ((defender, base * math.sqrt(ratio), attacker),
                                     (attacker, base / math.sqrt(ratio), defender)):
            frac = max(0.0, min(0.5, frac))
            total = 0
            for e in victim.elements.values():
                if e.category.upper() != "PERSONNEL" or e.count <= 0 or not victim.element_exposed(e):
                    continue
                n = sum(1 for _ in range(e.count) if rng.random() < frac)
                if n:
                    total += n
                    self.sim.events.push(self.sim.time + self.sim.combat._effect_delay_s(), "ELEMENT_LOSS",
                                         target=victim.uid, source=source.uid, element=e.eid, count=n,
                                         weapon="CLOSE_COMBAT", cue_type="DIRECT_FIRE")
            losses[victim.uid] = total
        stress = getattr(self.sim, "stress", None)
        if stress is not None and stress.enabled:
            shock = float(c["morale_shock_per_round"])
            # The side that is getting the worse of it loses cohesion fastest.
            stress._change_morale(defender, -shock * min(3.0, ratio), "CLOSE_COMBAT")
            stress._change_morale(attacker, -shock * min(3.0, 1.0 / ratio), "CLOSE_COMBAT")
        self.sim.log("CLOSE_COMBAT", attacker=attacker.uid, defender=defender.uid,
                     power_ratio=round(ratio, 2), attacker_losses=losses.get(attacker.uid, 0),
                     defender_losses=losses.get(defender.uid, 0))
