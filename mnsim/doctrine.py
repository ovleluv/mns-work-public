
from __future__ import annotations
import math
from typing import List, Tuple
from .model import Unit, UnitState, Track


class DoctrineEngine:
    """Local tactical behavior below the COA/order layer.

    Design rule:
    - COA/BML says WHAT the formation is trying to accomplish.
    - DoctrineEngine decides HOW the formation reacts locally to contacts/capability loss.
    - It must use only the unit's perceived tracks, never omniscient ground truth for decisions.

    The default rules are intentionally generic and scenario-tunable. They are not intended
    to reproduce any specific nation's doctrine.
    """

    def __init__(self, sim):
        self.sim = sim

    def profile(self, unit: Unit) -> dict:
        name=unit.metadata.get("doctrine_profile")
        if not name:
            return {}
        return dict(getattr(self.sim,"doctrine_profiles",{}).get(str(name),{}))

    def directive(self, unit: Unit, key: str, default=None):
        order=unit.current_order
        if order is None:
            return default
        return dict(getattr(order,"directives",{}) or {}).get(key,default)

    def setting(self, unit: Unit, key: str, combat_key: str | None = None, default=None):
        prof=self.profile(unit)
        if key in prof:
            return prof[key]
        if combat_key is None:
            combat_key=key
        return self.sim.combat_config.get(combat_key,default)

    def engagement_setting(self, unit: Unit, key: str, default=None):
        """Mission directive > doctrine profile > engine default for engagement positioning."""
        value=self.directive(unit,key,None)
        if value is not None:
            return value
        prof=self.profile(unit)
        if key in prof:
            return prof[key]
        return self.sim.combat_config.get(key,default)

    def desired_direct_engagement_range(self, unit: Unit, target: Unit, track: Track | None = None) -> float | None:
        """Return the doctrinal stand-off distance for the perceived target.

        This uses operational weapons and the unit's classification of the contact, never the
        target's hidden component inventory. STANDOFF preserves historical behavior by using
        the longest usable envelope.  COMBINED_ARMS closes until even the shortest relevant weapon
        can participate, allowing longer-ranged systems to fire while the formation advances.
        """
        if track is None:
            track=unit.local_tracks.get(target.uid)
        if track is None:
            return None
        ranges=[]
        for _,weapon in unit.operational_weapons():
            if weapon.capability.upper()=="INDIRECT_FIRE":
                continue
            # Perceived composition only: the formation plans stand-off against what it believes
            # the target contains, not against the live element inventory.
            if self.sim.combat.weapon_may_affect_track(weapon,track):
                ranges.append(float(weapon.range_m))
        if not ranges:
            return None
        policy=str(self.engagement_setting(unit,"engagement_range_policy","STANDOFF")).upper()
        if policy in ("COMBINED_ARMS","AGGRESSIVE","CLOSE_TO_ALL_WEAPONS"):
            base=min(ranges)
        elif policy in ("BALANCED","MIDRANGE"):
            base=(min(ranges)+max(ranges))*0.5
        else:
            base=max(ranges)
        fraction=float(self.engagement_setting(unit,"engagement_range_fraction",0.90))
        return max(1.0,base*max(0.25,min(1.0,fraction)))

    def should_close_for_direct_fire(self, unit: Unit, target: Unit, track: Track) -> bool:
        desired=self.desired_direct_engagement_range(unit,target,track)
        if desired is None:
            return True
        perceived=math.dist(unit.pos,track.estimated_pos)
        return perceived > desired

    def withdrawal_allowed(self, unit: Unit, *, autonomous: bool = False) -> bool:
        # Explicit mission intent has priority over normal local reaction doctrine. Physical
        # impossibility is still handled by the caller (e.g. immobilized units simply hold).
        if bool(self.directive(unit,"hold_at_all_costs",False)):
            return False
        allow=self.directive(unit,"allow_withdrawal",None)
        if allow is not None:
            return bool(allow)
        if autonomous and "allow_autonomous_withdrawal" in self.profile(unit):
            return bool(self.profile(unit)["allow_autonomous_withdrawal"] )
        return True

    def actionable_contacts(self, unit: Unit) -> List[Tuple[Unit, Track]]:
        out = []
        for tid, tr in unit.local_tracks.items():
            tgt = self.sim.units.get(tid)
            # Liveness is not read from ground truth: a destroyed target disappears from this list
            # only once the formation has battle-damage information (track state DESTROYED).
            if tgt and tgt.side != unit.side and tr.state != "DESTROYED" and self.sim._track_for(unit, tgt):
                out.append((tgt, tr))
        return out

    def _retreat_destination(self, unit: Unit, tr: Track, distance_m: float) -> tuple[float, float]:
        dx = unit.pos[0] - tr.estimated_pos[0]
        dy = unit.pos[1] - tr.estimated_pos[1]
        n = max(1.0, math.hypot(dx, dy))
        return (
            max(0.0, min(self.sim.world["width_m"], unit.pos[0] + dx / n * distance_m)),
            max(0.0, min(self.sim.world["height_m"], unit.pos[1] + dy / n * distance_m)),
        )

    def _move_away(self, unit: Unit, tr: Track, distance_m: float, dt: float) -> None:
        latched=unit.metadata.get("_doctrine_withdraw_dest")
        dest=tuple(latched) if latched else self._retreat_destination(unit, tr, distance_m)
        self.sim._move_toward(unit, dest, dt)
        if unit.metadata.get("_nav_no_path"):
            # The straight-away point is unreachable (building, lake, river): pick a reachable
            # withdrawal bearing once and keep it, instead of pushing into the obstacle.
            dx=unit.pos[0]-tr.estimated_pos[0]; dy=unit.pos[1]-tr.estimated_pos[1]
            away=math.degrees(math.atan2(dy,dx)) if abs(dx)+abs(dy)>1e-9 else unit.heading_deg+180.0
            alt=self._reachable_retreat_destination(unit, away, distance_m)
            unit.metadata.pop("_nav_no_path",None); unit.metadata.pop("_nav_no_path_destination",None)
            if alt is not None:
                unit.metadata["_doctrine_withdraw_dest"]=tuple(alt)
                self.sim._move_toward(unit, tuple(alt), dt)
        elif latched and math.dist(unit.pos,dest)<=float(self.sim.combat_config.get("order_arrival_m",3.0)):
            unit.metadata.pop("_doctrine_withdraw_dest",None)

    @staticmethod
    def _has_operational_weapon(unit: Unit) -> bool:
        return bool(unit.operational_weapons())

    @staticmethod
    def _has_tactical_mobility(unit: Unit) -> bool:
        if unit.unit_type.max_speed_mps <= 0.0:
            return False
        equipment=[e for e in unit.elements.values() if e.category.upper()=="EQUIPMENT" and e.count>0]
        if equipment:
            return any(e.mobile_item_count > 0 for e in equipment)
        return unit.personnel > 0

    def _combat_ineffective_behavior(self, unit: Unit, contacts: List[Tuple[Unit, Track]], dt: float) -> bool:
        """Prevent a formation with zero usable weapons from continuing an ATTACK order.

        FIREPOWER_KILL/disabled vehicles can leave an armor formation physically mobile while
        providing no operational weapon capability.  Previously such a unit kept executing its
        ATTACK order: it closed on an actionable track, then returned toward the objective when
        the track aged, producing conspicuous back-and-forth motion.

        Generic default doctrine is conservative: if a threat is currently actionable, make one
        bounded withdrawal away from the nearest perceived threat; otherwise hold/reorganize in
        place.  The withdrawal destination is latched instead of recomputed every tick so the unit
        does not run indefinitely.  Future recovery/maintenance doctrine can replace this behavior.
        """
        if self._has_operational_weapon(unit):
            unit.metadata.pop("combat_ineffective_withdraw_dest", None)
            unit.metadata.pop("combat_ineffective_threat", None)
            return False

        # Only override maneuver formations.  A sensor-only/C2 entity may legitimately have no weapon.
        if unit.branch not in ("ARMOR", "INFANTRY"):
            return False

        unit.target_id = None
        unit.metadata.pop("target_acquired_t", None)
        if not contacts or not self._has_tactical_mobility(unit) or not self.withdrawal_allowed(unit,autonomous=True):
            unit.state = UnitState.DEFENDING
            unit.metadata["tactical_reason"] = "NO OPERATIONAL WEAPON CAPABILITY / HOLD FOR RECOVERY"
            unit.metadata.pop("combat_ineffective_withdraw_dest", None)
            unit.metadata.pop("combat_ineffective_threat", None)
            return True

        tgt, tr = min(contacts, key=lambda x: math.dist(unit.pos, x[1].estimated_pos))
        threat_id = tgt.uid
        dest = unit.metadata.get("combat_ineffective_withdraw_dest")
        latched_threat = unit.metadata.get("combat_ineffective_threat")
        if dest is None or latched_threat != threat_id:
            distance = float(self.setting(unit,"combat_ineffective_withdraw_m",default=350.0))
            dest = self._retreat_destination(unit, tr, distance)
            unit.metadata["combat_ineffective_withdraw_dest"] = tuple(dest)
            unit.metadata["combat_ineffective_threat"] = threat_id

        unit.state = UnitState.RETREATING
        unit.metadata["tactical_reason"] = "NO OPERATIONAL WEAPON CAPABILITY / DISENGAGE"
        arrived = self.sim._move_toward(unit, tuple(dest), dt)
        if arrived:
            unit.state = UnitState.DEFENDING
            unit.metadata["tactical_reason"] = "COMBAT INEFFECTIVE / HOLD FOR RECOVERY"
        return True

    ADVANCING_ORDERS = ("ATTACK", "ATTACK_UNIT", "DESTROY_UNIT", "ATTACK_STRUCTURE")

    def _threat_bearing_away(self, unit: Unit, contacts) -> float | None:
        if contacts:
            _, tr = min(contacts, key=lambda x: math.dist(unit.pos, x[1].estimated_pos))
            dx = unit.pos[0] - tr.estimated_pos[0]; dy = unit.pos[1] - tr.estimated_pos[1]
            if abs(dx) + abs(dy) > 1e-9:
                return math.degrees(math.atan2(dy, dx))
        if self.sim.time <= float(unit.metadata.get("threat_cue_until_t", -1e9)):
            return float(unit.metadata.get("threat_cue_heading_deg", unit.heading_deg)) + 180.0
        return None

    def _stress_behavior(self, unit: Unit, contacts, dt: float) -> bool:
        stress = getattr(self.sim, "stress", None)
        if stress is None or not stress.enabled:
            return False
        state = stress.morale_state(unit)
        order = unit.current_order
        kind = str(order.kind).upper() if order else ""
        if state == "BROKEN":
            if self._has_tactical_mobility(unit) and kind != "RETREAT":
                dest = unit.metadata.get("_broken_withdraw_dest")
                if dest is None:
                    away = self._threat_bearing_away(unit, contacts)
                    if away is not None:
                        dist = float(stress.cfg().get("broken_withdraw_m", 400.0))
                        dest = self._reachable_retreat_destination(unit, away, dist)
                    if dest is not None:
                        unit.metadata["_broken_withdraw_dest"] = tuple(dest)
                if dest is not None and math.dist(unit.pos, tuple(dest)) > float(self.sim.combat_config.get("order_arrival_m", 3.0)):
                    unit.state = UnitState.RETREATING
                    unit.metadata["tactical_reason"] = "MORALE BROKEN / FALLING BACK"
                    self.sim._move_toward(unit, tuple(dest), dt)
                    return True
            unit.state = UnitState.DEFENDING
            unit.metadata["tactical_reason"] = "MORALE BROKEN / REORGANISING"
            return True
        unit.metadata.pop("_broken_withdraw_dest", None)
        if kind in self.ADVANCING_ORDERS and (stress.pinned(unit) or state == "SHAKEN"):
            unit.state = UnitState.DEFENDING
            unit.metadata["tactical_reason"] = ("PINNED BY FIRE / GO TO GROUND" if stress.pinned(unit)
                                                else "SHAKEN / ATTACK STALLED")
            return True
        return False

    def _counterfire_displacement(self, unit: Unit, dt: float) -> bool:
        if not bool(self.setting(unit, "artillery_displace_on_counterfire", default=True)):
            return False
        if not any(str(w.capability).upper()=="INDIRECT_FIRE" for e in unit.elements.values() for w in e.weapons):
            return False
        dest = unit.metadata.get("_scoot_dest")
        if dest is None:
            cue = str(unit.metadata.get("threat_cue_type", "")).upper()
            if cue != "INDIRECT_FIRE" or self.sim.time > float(unit.metadata.get("threat_cue_until_t", -1e9)):
                return False
            if (not bool(self.directive(unit, "allow_artillery_displacement", True))
                    or not self.withdrawal_allowed(unit, autonomous=True) or not self._has_tactical_mobility(unit)):
                return False
            cooldown = float(self.setting(unit, "artillery_displace_cooldown_s", default=240.0))
            if self.sim.time - float(unit.metadata.get("_last_scoot_t", -1e9)) < cooldown:
                return False
            away = float(unit.metadata.get("threat_cue_heading_deg", unit.heading_deg)) + 180.0
            dist = float(self.setting(unit, "artillery_counterfire_displace_m", default=500.0))
            dest = self._reachable_retreat_destination(unit, away, dist)
            if dest is None:
                return False
            unit.metadata["_scoot_dest"] = tuple(dest)
            unit.metadata["_last_scoot_t"] = self.sim.time
            self.sim.log("ARTILLERY_DISPLACE_COUNTERFIRE", unit=unit.uid, to=[round(dest[0], 1), round(dest[1], 1)])
        if math.dist(unit.pos, tuple(dest)) <= float(self.sim.combat_config.get("order_arrival_m", 3.0)):
            unit.metadata.pop("_scoot_dest", None)
            return False
        unit.state = UnitState.MOVING
        unit.metadata["tactical_reason"] = "COUNTERFIRE / DISPLACING"
        self.sim._move_toward(unit, tuple(dest), dt)
        return True

    STATIC_ORDERS = ("HOLD", "DEFEND", "WAIT")

    def _max_reply_range(self, unit: Unit, track) -> float:
        best = 0.0
        for _, w in unit.operational_weapons():
            if str(w.capability).upper() == "INDIRECT_FIRE":
                continue
            if track is None or self.sim.combat.weapon_can_affect_perceived(w, track):
                best = max(best, float(w.range_m))
        return best

    def _outranged_reaction(self, unit: Unit, dt: float) -> bool:
        cfg = dict(self.sim.combat_config.get("outranged_reaction", {}) or {})
        if not bool(cfg.get("enabled", True)):
            return False
        order = unit.current_order
        kind = str(order.kind).upper() if order else "NONE"
        latched = unit.metadata.get("_outranged_withdraw_dest")
        if latched is not None:
            if math.dist(unit.pos, tuple(latched)) <= float(self.sim.combat_config.get("order_arrival_m", 3.0)):
                unit.metadata.pop("_outranged_withdraw_dest", None)
                unit.metadata.pop("_outranged_since", None)
                return False
            unit.state = UnitState.RETREATING
            unit.metadata["tactical_reason"] = "OUTRANGED / WITHDRAW OUT OF RANGE"
            self.sim._move_toward(unit, tuple(latched), dt)
            return True
        if kind not in self.STATIC_ORDERS and order is not None:
            unit.metadata.pop("_outranged_since", None)
            return False
        now = self.sim.time
        cue_live = (now <= float(unit.metadata.get("threat_cue_until_t", -1e9))
                    and str(unit.metadata.get("threat_cue_type", "")).upper() == "DIRECT_FIRE")
        if not cue_live or not self.withdrawal_allowed(unit, autonomous=True) or not self._has_tactical_mobility(unit):
            unit.metadata.pop("_outranged_since", None)
            return False
        src = unit.metadata.get("threat_cue_source_uid")
        tr = unit.local_tracks.get(src) if src else None
        reply = self._max_reply_range(unit, tr)
        if tr is not None and tr.state not in ("LOST", "DESTROYED"):
            perceived = math.dist(unit.pos, tr.estimated_pos)
            can_answer = reply > 0.0 and perceived <= reply
        else:
            can_answer = False            # fired on by an enemy it cannot even see
        if can_answer:
            unit.metadata.pop("_outranged_since", None)
            return False
        since = unit.metadata.setdefault("_outranged_since", now)
        if now - since < float(cfg.get("tolerate_s", 20.0)):
            return False
        away = float(unit.metadata.get("threat_cue_heading_deg", unit.heading_deg)) + 180.0
        dest = self._reachable_retreat_destination(unit, away, float(cfg.get("withdraw_m", 350.0)))
        if dest is None:
            return False
        unit.metadata["_outranged_withdraw_dest"] = tuple(dest)
        self.sim.log("OUTRANGED_WITHDRAW", unit=unit.uid, source=src, reply_range_m=round(reply, 1),
                     order=kind)
        unit.state = UnitState.RETREATING
        unit.metadata["tactical_reason"] = "OUTRANGED / WITHDRAW OUT OF RANGE"
        self.sim._move_toward(unit, tuple(dest), dt)
        return True

    def _indirect_fire_dispersion_reaction(self, unit: Unit) -> None:
        if not bool(self.setting(unit,"indirect_fire_auto_disperse",default=True)):
            return
        if self.directive(unit,"allow_indirect_fire_dispersion",True) is False:
            return
        now=self.sim.time
        cue=str(unit.metadata.get("threat_cue_type","")).upper()
        cue_until=float(unit.metadata.get("threat_cue_until_t",-1e9))
        active_until=float(unit.metadata.get("indirect_disperse_until_t",-1e9))
        if cue=="INDIRECT_FIRE" and now<=cue_until:
            duration=float(self.setting(unit,"indirect_fire_auto_disperse_duration_s",default=120.0))
            if now>active_until:
                unit.metadata.setdefault("pre_indirect_dispersion_posture",
                                         str(unit.metadata.get("dispersion_posture","NORMAL")).upper())
            unit.metadata["indirect_disperse_until_t"]=max(active_until,now+duration)
            self.sim.set_formation_posture(unit,"DISPERSED")
            return
        if active_until>-1e8 and now>active_until:
            prior=str(unit.metadata.pop("pre_indirect_dispersion_posture","NORMAL")).upper()
            unit.metadata.pop("indirect_disperse_until_t",None)
            self.sim.set_formation_posture(unit,prior)

    def _reachable_retreat_destination(self, unit: Unit, bearing_away_deg: float, distance_m: float):
        """Pick a nearby passable/reachable withdrawal point without crossing an impassable barrier."""
        for delta in (0.0, 30.0, -30.0, 60.0, -60.0, 90.0, -90.0, 135.0, -135.0, 180.0):
            a=math.radians(bearing_away_deg+delta)
            cand=(
                max(0.0,min(self.sim.world["width_m"],unit.pos[0]+math.cos(a)*distance_m)),
                max(0.0,min(self.sim.world["height_m"],unit.pos[1]+math.sin(a)*distance_m)),
            )
            if math.dist(cand,unit.pos)<5.0:
                continue
            if self.sim.terrain is None:
                return cand
            route=self.sim.terrain.plan_route(unit,cand)
            if len(route)>1:
                return cand
        return None

    def autonomous_fallback(self, unit: Unit, dt: float) -> bool:
        """Local behavior after a higher-level mission is impossible because no route exists.

        Default behavior is HOLD.  A badly attrited mobile formation may make a short local
        withdrawal, but only using perceived contacts or a recent incoming-fire bearing.
        """
        self._indirect_fire_dispersion_reaction(unit)
        threshold=float(self.setting(unit,"unreachable_high_loss_ratio",default=0.35))
        if unit.loss_ratio < threshold or not self._has_tactical_mobility(unit) or not self.withdrawal_allowed(unit,autonomous=True):
            return False
        dest=unit.metadata.get("unreachable_fallback_withdraw_dest")
        if dest is None:
            contacts=self.actionable_contacts(unit)
            away=None
            if contacts:
                _,tr=min(contacts,key=lambda x:math.dist(unit.pos,x[1].estimated_pos))
                dx=unit.pos[0]-tr.estimated_pos[0]; dy=unit.pos[1]-tr.estimated_pos[1]
                if abs(dx)+abs(dy)>1e-9:
                    away=math.degrees(math.atan2(dy,dx))
            elif self.sim.time <= float(unit.metadata.get("threat_cue_until_t",-1e9)):
                away=float(unit.metadata.get("threat_cue_heading_deg",unit.heading_deg))+180.0
            if away is None:
                return False
            distance=float(self.setting(unit,"unreachable_withdraw_m",default=250.0))
            dest=self._reachable_retreat_destination(unit,away,distance)
            if dest is None:
                return False
            unit.metadata["unreachable_fallback_withdraw_dest"]=tuple(dest)
        unit.state=UnitState.RETREATING
        unit.metadata["tactical_reason"]="NO ROUTE + HIGH LOSSES / LOCAL WITHDRAWAL"
        arrived=self.sim._move_toward(unit,tuple(dest),dt)
        if arrived:
            unit.metadata.pop("unreachable_fallback_withdraw_dest",None)
            unit.state=UnitState.DEFENDING
            unit.metadata["tactical_reason"]="NO ROUTE / WITHDRAWAL COMPLETE / HOLD"
        return True

    def step(self, unit: Unit, dt: float) -> bool:
        if self.sim._update_crew_readiness(unit):
            return True
        self._indirect_fire_dispersion_reaction(unit)
        contacts = self.actionable_contacts(unit)

        # A mobile formation with every weapon firepower-killed must not continue closing on
        # contacts merely because its higher-level ATTACK order remains active.
        if self._combat_ineffective_behavior(unit, contacts, dt):
            return True

        # Morale and suppression override the COA order: broken formations withdraw and
        # reorganise, pinned/shaken ones stop advancing and go to ground (they keep firing).
        if self._stress_behavior(unit, contacts, dt):
            return True

        # Under sustained fire it cannot answer (outranged, or the shooter is unseen), a
        # non-attacking formation pulls back out of the beaten zone instead of dying in place.
        if self._outranged_reaction(unit, dt):
            return True

        # Shoot and scoot: a firing battery that starts receiving (counter-)battery fire moves.
        if self._counterfire_displacement(unit, dt):
            return True

        if not contacts:
            return False

        # Infantry: if an armor contact is actionable but all anti-armor capability has
        # disappeared (team killed, weapon lost, or ammunition depleted), break contact.
        if unit.branch == "INFANTRY":
            armor = [x for x in contacts if self.sim.combat.track_indicates_armor(x[1])]
            break_if_no_at=bool(self.setting(unit,"infantry_break_contact_if_no_at",default=True))
            allow_break=bool(self.directive(unit,"allow_break_contact",True))
            if armor and not unit.capability_available("ANTI_ARMOR") and break_if_no_at and allow_break:
                tgt, tr = min(armor, key=lambda x: math.dist(unit.pos, x[1].estimated_pos))
                if not self.withdrawal_allowed(unit,autonomous=True):
                    unit.state=UnitState.DEFENDING
                    unit.metadata["tactical_reason"]="ARMOR CONTACT / NO ANTI-ARMOR / WITHDRAWAL PROHIBITED"
                    return True
                unit.state = UnitState.RETREATING
                unit.metadata["tactical_reason"] = "ARMOR CONTACT / NO ANTI-ARMOR CAPABILITY / BREAK CONTACT"
                unit.metadata["doctrine_target"] = tgt.uid
                self._move_away(unit, tr, float(self.setting(unit,"infantry_break_contact_m",default=260.0)), dt)
                return True

        # Artillery: close direct contact causes displacement rather than deliberate close combat.
        if unit.branch == "ARTILLERY":
            enabled=bool(self.setting(unit,"artillery_displace_on_close_contact",default=True))
            allow=bool(self.directive(unit,"allow_artillery_displacement",True))
            threshold = float(self.setting(unit,"artillery_displace_contact_m",default=350.0))
            near = [x for x in contacts if math.dist(unit.pos, x[1].estimated_pos) <= threshold]
            if enabled and allow and near:
                tgt, tr = min(near, key=lambda x: math.dist(unit.pos, x[1].estimated_pos))
                if not self.withdrawal_allowed(unit,autonomous=True):
                    unit.state=UnitState.DEFENDING
                    unit.metadata["tactical_reason"]="DIRECT CONTACT / DISPLACEMENT PROHIBITED"
                    return True
                unit.state = UnitState.RETREATING
                unit.metadata["tactical_reason"] = "DIRECT CONTACT / ARTILLERY DISPLACE"
                unit.metadata["doctrine_target"] = tgt.uid
                self._move_away(unit, tr, float(self.setting(unit,"artillery_displace_m",default=400.0)), dt)
                return True

        return False
