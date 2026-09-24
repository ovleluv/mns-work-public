from __future__ import annotations
from typing import Dict
import json, math, random
from .model import Unit, UnitState, Order, Track
from .events import EventQueue
from .bml import compile_order_fragment, ConditionEvaluator
from .combat import CombatResolver
from .doctrine import DoctrineEngine
from .indirect_fire import IndirectFireResolver
from .belief import ContactBeliefPolicy
from .fire_control import FireControlEngine
from .terrain import TerrainModel
from .damage import DamageResolver
from .environment import EnvironmentObservationModel
from .communications import CommunicationNetwork

from .perception import PerceptionMixin
from .composition import CompositionMixin
from .orders import OrderExecutionMixin
from .engagement import EngagementMixin


class Simulation(PerceptionMixin, CompositionMixin, OrderExecutionMixin, EngagementMixin):
    def __init__(self, seed=7):
        self.time = 0.0
        self.seed = seed
        self.units: Dict[str, Unit] = {}
        # Ordinary individual-infantry observation fallback for programmatic simulations.
        # Scenario loading replaces this with the local INF_IND template.
        self.dismounted_crew_sensor = {"detection_range_m": 500.0,
                                       "visual_sensor": {"forward_range_m": 500.0,
                                                         "watch_slew_deg_per_s": 70.0}}
        self.events = EventQueue(); self.rng = random.Random(seed); self.logs = []
        self.objectives = {}; self.world = {"width_m": 4000.0, "height_m": 4000.0}
        self.speed = 1.0; self.paused = False; self.engagements = []
        self._next_sensor_update = 0.0
        self.terrain = TerrainModel({})
        self.artillery_doctrine_profiles = {}
        self.doctrine_profiles = {}
        self.targeting_doctrine = {}
        self._last_report_sent = {}
        self._last_engagement_report = {}
        self.combat_config = {
            "engagement_link_m": 420.0,
            "artillery_support_radius_m": 900.0,
            "sensor_update_s": 1.0,
            "track_action_confidence": 0.35,
            "track_stale_s": 18.0,
            "track_lost_s": 45.0,
            "c2_share_delay_min_s": 3.0,
            "c2_share_delay_max_s": 10.0,
            "attack_track_memory_s": 90.0,
            "attack_search_arrival_m": 25.0,
            "attack_search_hold_s": 12.0,
             "counter_battery_share_probability": 0.90,
            "proximity_contact_m": 60.0,
            "proximity_contact_error_m": 12.0,
            # Formation-level principal observation / engagement orientation.  These are not literal
            # eyeball/head or turret mechanical slew rates; they include command, scanning and crew
            # orientation delay at the represented echelon.
            "visual_forward_fov_deg": 80.0,
            "visual_all_round_awareness_m": 180.0,
            "visual_watch_slew_deg_per_s": 30.0,
            "visual_sensor_profiles": {
                "DEFAULT":{"forward_fov_deg":80.0,"all_round_awareness_m":180.0,"watch_slew_deg_per_s":30.0,"sensor_mode":"VISUAL"},
                "INFANTRY":{"forward_fov_deg":90.0,"all_round_awareness_m":220.0,"watch_slew_deg_per_s":45.0,"sensor_mode":"VISUAL"},
                "ARMOR":{"forward_fov_deg":60.0,"all_round_awareness_m":160.0,"watch_slew_deg_per_s":20.0,"sensor_mode":"VISUAL"},
                "ARTILLERY":{"forward_fov_deg":75.0,"all_round_awareness_m":180.0,"watch_slew_deg_per_s":30.0,"sensor_mode":"VISUAL"},
            },
            "direct_fire_requires_watch_alignment": True,
            "direct_fire_watch_edge_margin_deg": 5.0,
            "target_priority": {"INFANTRY":{"INFANTRY":1.0,"ARMOR":1.1,"ARTILLERY":1.2},
                                "ARMOR":{"INFANTRY":0.9,"ARMOR":1.3,"ARTILLERY":1.1},
                                "ARTILLERY":{"INFANTRY":1.1,"ARMOR":1.0,"ARTILLERY":1.2}},
            "infantry_break_contact_m": 260.0,
            "artillery_displace_contact_m": 350.0,
            "artillery_displace_m": 400.0,
            "indirect_friendly_fire": True,
            # Persistent enemy-order-of-battle belief. These affect display/intelligence memory,
            # not whether a stale track is valid for firing.
            "belief_grace_s": 90.0,
            "belief_half_life_s": 900.0,
            "belief_floor_confidence": 0.28,
            "belief_display_threshold": 0.25,
            # Explicit communications abstraction. Scenario/default config may override these.
            "communications": {
                "routing_mode": "SIDE_WIDE",
                "default_link": {"name":"TACTICAL_RADIO","medium":"RADIO",
                                 "min_delay_s":1.0,"max_delay_s":4.0,"reliability":0.995,
                                 "max_range_m":None,"jam_resistance":0.5},
                "global_jamming_strength": 0.0,
                "engagement_reports_enabled": True,
                "engagement_report_probability": 0.88,
                "engagement_report_min_interval_s": 15.0,
            },
            "shared_situational_cue": {"min_confidence":0.28,"memory_s":18.0,"engaged_memory_s":24.0,
                                          "replace_score_ratio":1.10,
                                          "kind_weight":{"CONTACT":1.0,"CONTACT_ENGAGED":1.25}},
            "navigation": {"mode":"SPARSE_ASTAR", "sample_spacing_m":20.0,
                           "waypoint_arrival_m":10.0, "bridge_portal_margin_m":8.0,
                           "replan_deviation_m":80.0, "replan_destination_shift_m":75.0, "local_direct_route_m":1200.0},
        }
        self.combat = CombatResolver(self)
        self.doctrine = DoctrineEngine(self)
        self.indirect_fire = IndirectFireResolver(self)
        self.belief = ContactBeliefPolicy(self)
        self.fire_control = FireControlEngine(self)
        self.damage = DamageResolver(self)
        self.environment = EnvironmentObservationModel(self.combat_config)
        self.communications = CommunicationNetwork(self)
        from .stress import CombatStressModel
        self.stress = CombatStressModel(self)
        from .assault import AssaultModel
        self.assault = AssaultModel(self)

    def set_formation_posture(self, unit_or_uid, posture: str) -> bool:
        """Set spatial dispersion/shape posture without changing the unit's operational order.

        This is deliberately a geometry interface, not a tactical decision rule. Future doctrine,
        BML, minefield reaction, artillery-survival logic, terrain restrictions, or an RL agent may
        call it.
        """
        unit=self.units.get(unit_or_uid) if isinstance(unit_or_uid,str) else unit_or_uid
        if unit is None:return False
        posture=str(posture).upper()
        allowed=self.combat_config.get("formation_posture_modifiers",{})
        if posture not in allowed:return False
        old=str(unit.metadata.get("dispersion_posture","NORMAL")).upper()
        unit.metadata["dispersion_posture"]=posture
        if old!=posture:self.log("FORMATION_POSTURE",unit=unit.uid,old=old,new=posture)
        return True

    def add_unit(self, unit: Unit):
        if unit.uid in self.units:
            raise ValueError(f"Unit ID already exists: {unit.uid}")
        self.units[unit.uid] = unit
        self.log("UNIT_ADD", unit=unit.uid, side=unit.side.value, pos=unit.pos,
                 personnel=unit.personnel, equipment=unit.equipment)

    def log(self, kind: str, **data): self.logs.append({"t": round(self.time,3), "kind":kind, **data})

    def issue_order(self, uid: str, order: Order):
        order.deadline_reported = False
        self.units[uid].order_queue.append(order); self.log("ORDER_ISSUED",unit=uid,order=order.kind,order_id=order.order_id)

    def tick(self, dt: float):
        dt=float(dt); speed=float(self.speed)
        if not math.isfinite(dt) or dt<0.0 or not math.isfinite(speed) or speed<0.0:
            raise ValueError("Simulation time step and speed must be finite and non-negative")
        if self.paused or speed==0.0: return
        scaled=dt*speed
        if not math.isfinite(scaled) or not math.isfinite(self.time+scaled):
            raise ValueError("Simulation time step exceeds the finite clock range")
        self.step(scaled)

    def step(self, sim_dt: float):
        """Advance simulation time by ``sim_dt`` seconds (not scaled by ``speed``)."""
        sim_dt=float(sim_dt)
        if not math.isfinite(sim_dt) or sim_dt<0.0:
            raise ValueError(f"simulation step must be finite and non-negative: {sim_dt!r}")
        from . import model as _model
        _model.STATE_EPOCH[0] += 1
        start=self.time; end=start+sim_dt
        if not math.isfinite(end):
            raise ValueError("Simulation time step exceeds the finite clock range")
        # Positions at the start of the step, used only for render interpolation between steps.
        self._step_start_pos={uid:u.pos for uid,u in self.units.items()}
        # Each event is handled at its own timestamp, so follow-on delays (C2 hops, FDC, damage)
        # accumulate from the true event time instead of being rounded up to the tick boundary.
        for ev in self.events.pop_due(end):
            self.time=max(start,float(ev.time))
            self._handle_event(ev.kind, ev.payload)
        self.time=end
        self._resolve_collapsed_bridges()
        self.stress.update(sim_dt)
        self.assault.update(sim_dt)
        for u in list(self.units.values()):
            if u.alive:
                self._step_unit(u,sim_dt)
        self._update_dig_in(start)
        # Scans stay on a fixed k*interval grid, so the scan count over a run does not depend
        # on the integration step or the UI speed multiplier.
        interval=max(1e-3,float(self.combat_config.get("sensor_update_s",1.0)))
        if self.time >= self._next_sensor_update:
            self._sensor_step()
            self._next_sensor_update += interval
            if self._next_sensor_update <= self.time:
                self._next_sensor_update = self.time + interval
        self._combat_step()

    # ---------- prepared positions ----------
    def _update_dig_in(self, step_start: float):
        """Track how long each formation has held a stationary defensive posture."""
        prepared_at_start=bool(self.combat_config.get("initial_defenders_prepared",True))
        dig=float(self.combat_config.get("dig_in_time_s",900.0))
        for u in self.units.values():
            if not u.alive:
                continue
            moved=float(u.metadata.get("_moved_at",-1e9))>=step_start
            if u.state==UnitState.DEFENDING and not moved:
                if "_defending_since" not in u.metadata:
                    # Positions occupied at scenario start are treated as already prepared.
                    first=step_start<=1e-9 and prepared_at_start
                    u.metadata["_defending_since"]=(step_start-dig) if first else step_start
            else:
                u.metadata.pop("_defending_since",None)

    def dig_in_fraction(self, unit: Unit) -> float:
        """0 when not defending; hasty position -> fully prepared over ``dig_in_time_s``."""
        if unit.state!=UnitState.DEFENDING:
            return 0.0
        since=unit.metadata.get("_defending_since")
        hasty=float(self.combat_config.get("hasty_position_fraction",0.5))
        if since is None:
            return hasty
        dig=max(1.0,float(self.combat_config.get("dig_in_time_s",900.0)))
        return hasty+(1.0-hasty)*min(1.0,max(0.0,self.time-float(since))/dig)

    def realtime_alpha(self, max_sim_step_s: float = 0.25) -> float:
        """Fraction of the next fixed step already accumulated (0..1), for render interpolation."""
        fixed=max(float(max_sim_step_s),0.01)
        return max(0.0,min(1.0,getattr(self,"_realtime_accumulator",0.0)/fixed))

    def display_position(self, unit: Unit, alpha: float):
        """Position interpolated between the last two fixed steps (presentation only)."""
        prev=getattr(self,"_step_start_pos",{}).get(unit.uid)
        if prev is None or not unit.active:
            return unit.pos
        a=max(0.0,min(1.0,float(alpha)))
        return (prev[0]+(unit.pos[0]-prev[0])*a, prev[1]+(unit.pos[1]-prev[1])*a)

    def advance_realtime(self, wall_dt: float, max_sim_step_s: float = 0.25):
        """Advance from UI wall-clock time with a fixed simulation step.

        Wall-clock time is converted to simulation time and accumulated; only whole fixed steps
        of ``max_sim_step_s`` are executed.  Results therefore depend on neither the render frame
        rate nor the 1x..32x speed setting, only on the fixed step.
        """
        wall_dt=float(wall_dt); speed=float(self.speed)
        if not math.isfinite(wall_dt) or wall_dt<0.0 or not math.isfinite(speed) or speed<0.0:
            raise ValueError("Wall time step and simulation speed must be finite and non-negative")
        if self.paused or wall_dt==0.0 or speed==0.0:
            return
        fixed=max(float(max_sim_step_s),0.01)
        # Cap one call's backlog so a stalled frame cannot trigger a long catch-up freeze.
        budget=min(float(wall_dt)*speed, fixed*int(self.combat_config.get("max_steps_per_frame",256)))
        if not math.isfinite(budget):
            raise ValueError("Accelerated time step exceeds the finite clock range")
        self._realtime_accumulator=getattr(self,"_realtime_accumulator",0.0)+budget
        while self._realtime_accumulator >= fixed-1e-9:
            self._realtime_accumulator-=fixed
            self.step(fixed)

    def _update_crew_readiness(self, u: Unit) -> bool:
        """Suspend orders on crew loss; retain the physical unit and pending plan.

        Re-evaluate after damage and before orders so a deadline/condition cannot
        make an unstaffed platform move. Recovery requires actual usable personnel.
        """
        reason = u.crew_failure_reason
        previous = u.metadata.get("crew_failure_reason")
        if reason:
            if previous != reason:
                self.log("COMBAT_INEFFECTIVE", unit=u.uid, reason=reason,
                         personnel=u.personnel, equipment=u.equipment,
                         order_id=u.current_order.order_id if u.current_order else None)
                self._clear_navigation_state(u)
            u.metadata["crew_failure_reason"] = reason
            u.metadata["tactical_reason"] = reason + " / COMBAT INEFFECTIVE / ORDERS SUSPENDED"
            u.state = UnitState.COMBAT_INEFFECTIVE
            u.target_id = None
            u.metadata.pop("target_acquired_t", None)
            return True
        if previous:
            u.metadata.pop("crew_failure_reason", None)
            u.metadata.pop("tactical_reason", None)
            if u.alive:
                u.state = UnitState.IDLE
                self.log("COMBAT_CAPABILITY_RESTORED", unit=u.uid,
                         personnel=u.personnel, equipment=u.equipment)
        return False


    # Public, UI-facing read-only queries (frontends must not call underscored engine internals).


    def _handle_event(self,kind,p):
        if kind=="CB_TRACK_READY":
            radar=self.units.get(p.get("radar")); target=self.units.get(p.get("target"))
            # The projectile trajectory already contains the information needed for a point-of-origin
            # solution.  The firing unit becoming non-operational during processing must not erase it.
            if not radar or not radar.alive or not target:
                return
            radar_el=radar.elements.get(p.get("radar_element"))
            if not radar_el or not self._radar_element_operational(radar,radar_el):
                self.log("CB_TRACK_ABORTED",radar=p.get("radar"),source=p.get("target"),reason="RADAR_DISABLED")
                return
            tr=Track(track_id=f"{radar.uid}:{target.uid}",target_id=target.uid,
                     estimated_pos=tuple(p["estimated_pos"]),position_error_m=float(p["position_error_m"]),
                     classification="ARTILLERY",confidence=float(p["confidence"]),last_seen_time=float(p.get("observation_time",self.time)),
                     observations=int(p.get("observations",1)),source="COUNTER_BATTERY",observation_zone="SENSOR",state=p.get("state","CLASSIFIED"),
                     belief_confidence=max(0.70,float(p["confidence"])),existence_confirmed=True,
                     last_confirmed_time=float(p.get("observation_time",self.time)))
            prev_tr=radar.local_tracks.get(target.uid)
            if prev_tr is not None and prev_tr.state=="DESTROYED":
                return
            self.belief.on_observation(tr,"ARTILLERY",observed_at=tr.last_seen_time)
            radar.local_tracks[target.uid]=tr
            self.log("CB_RADAR_DETECT",radar=radar.uid,source=target.uid,
                     estimated_pos=[round(tr.estimated_pos[0],1),round(tr.estimated_pos[1],1)],
                     position_error_m=round(tr.position_error_m,1),confidence=round(tr.confidence,2),
                     distance=round(float(p.get("distance",0)),1),weapon=p.get("weapon"),
                     projectile_count=int(p.get("projectile_count",1)),
                     trajectory_opportunities=int(p.get("trajectory_opportunities",1)),
                     single_projectile_p=round(float(p.get("single_projectile_p",0.0)),3),
                     salvo_detect_p=round(float(p.get("salvo_detect_p",0.0)),3))
            if self.rng.random() < float(self.combat_config.get("counter_battery_share_probability",0.90)):
                payload={"target":target.uid,"side":radar.side.value,"estimated_pos":tr.estimated_pos,
                         "position_error_m":tr.position_error_m*1.08,"classification":"ARTILLERY",
                         "confidence":max(.25,tr.confidence*.94),"state":tr.state,
                         "track_source":"COUNTER_BATTERY","observation_time":tr.last_seen_time}
                n=self.communications.broadcast_side(radar.uid,"TRACK_REPORT",payload,priority=30)
                self.log("TRACK_DISSEMINATION",source=radar.uid,target=target.uid,recipients=n,origin="COUNTER_BATTERY")
            return
        if kind=="TRACK_REPORT_TO_HQ":
            # HQ/C2 processing remains an explicit delay. Actual dissemination then traverses the
            # communications layer recipient by recipient (radio/wire/jamming-ready).
            delay=self.rng.uniform(
                float(self.combat_config.get("c2_dissemination_delay_min_s",8.0)),
                float(self.combat_config.get("c2_dissemination_delay_max_s",18.0))
            )
            self.events.push(self.time+delay,"C2_DISSEMINATE_TRACK",report=dict(p))
            self.log("TRACK_REPORT_RECEIVED_HQ",source=p.get("source"),target=p.get("target"),
                     dissemination_delay_s=round(delay,1))
            return
        if kind=="C2_DISSEMINATE_TRACK":
            q=dict(p.get("report",{})); source=q.get("source")
            q.setdefault("report_origin_uid",source)
            sender=self.units.get(source)
            relay_uid=source
            if sender is None or not sender.can_communicate:
                # Once HQ has the report, losing the observer must not erase the message.
                side=sender.side.value if sender else q.get("side")
                relay=next((u for u in self.units.values()
                            if u.can_communicate and u.side.value==side),None)
                if relay is not None:
                    relay_uid=relay.uid
                    self._receive_comm_message(relay,{"message_type":"TRACK_REPORT",
                        "sender_uid":source,"channel":"C2_RELAY","payload":q})
            n=self.communications.broadcast_side(relay_uid,"TRACK_REPORT",q,priority=20)
            details={"source":source,"target":q.get("target"),"recipients":n,"origin":"C2"}
            if relay_uid!=source:
                details["relay"]=relay_uid
            self.log("TRACK_DISSEMINATION",**details)
            return
        if kind=="COMM_DELIVER":
            msg=dict(p.get("message",{})); recv=self.units.get(msg.get("recipient_uid"))
            if not recv or not recv.can_communicate: return
            self._receive_comm_message(recv,msg)
            return
        if kind=="FIRE_MISSION_READY":
            self.fire_control.on_ready(p)
            return
        if kind=="ARTY_IMPACT_RESOLVE":
            self.indirect_fire.resolve_impact(p)
            return
        if kind=="TRACK_SHARE":
            # Backward-compatible legacy event: route through the communications layer instead of
            # copying knowledge to every friendly entity instantaneously.
            source=p.get("source")
            self.communications.broadcast_side(source,"TRACK_REPORT",dict(p),priority=20)
            return
        if kind=="EQUIPMENT_EFFECT":
            t=self.units.get(p["target"])
            if not t:return
            el=t.elements.get(p["element"])
            item_index=p.get("item_index")
            if p.get("item_id") is not None:
                # The addressed item may have been detached into a vehicle-level child since the
                # effect was scheduled; follow the physical item rather than the list position.
                located=self._locate_equipment_item(t,str(p["element"]),str(p["item_id"]))
                if located is None:
                    self.log("EQUIPMENT_EFFECT_TARGET_GONE",target=t.uid,element=p.get("element"),item_id=p.get("item_id"))
                    return
                t,el,item_index=located
            if not t.alive:return
            if not el or not el.alive:return
            reason=str(p.get("reason",""))
            cue_type="INDIRECT_FIRE" if reason.startswith("ARTILLERY_") else "DIRECT_FIRE"
            self._register_threat_cue(t,p.get("source"),cue_type)
            strength_before=t.current_strength
            self.damage.apply_equipment_effect(t,el,p.get("effect","DISABLED"),
                                               source=p.get("source",""),weapon=p.get("weapon",""),
                                               reason=reason,item_index=item_index)
            if t.initial_strength>0 and not t.metadata.get("depleted_by_detachment"):
                self.stress.on_losses(t,max(0.0,strength_before-t.current_strength)/t.initial_strength,el)
            if not t.alive:
                if t.metadata.get("depleted_by_detachment"):
                    return  # every vehicle moved to a detached child; nothing was killed
                if t.state!=UnitState.DESTROYED:
                    t.state=UnitState.DESTROYED; self.log("DESTROYED",unit=t.uid,source=p.get("source"))
                self._on_unit_destroyed(t,p.get("source"))
                return
            self._check_reactive_branches(t)
            return
        if kind=="ELEMENT_LOSS":
            t=self.units.get(p["target"])
            if not t or not t.alive:return
            el=t.elements.get(p["element"])
            if not el or el.count<=0:return
            requested=int(p.get("count",1)); actual=min(el.count,requested)
            strength_before=t.current_strength; el.count-=actual
            if t.initial_strength>0:
                self.stress.on_losses(t,max(0.0,strength_before-t.current_strength)/t.initial_strength,el)
            self._register_threat_cue(t,p.get("source"),str(p.get("cue_type","DIRECT_FIRE")))
            self.log("ELEMENT_LOSS",target=t.uid,source=p.get("source"),element=el.eid,role=el.role,count=actual,remaining=el.count,personnel=t.personnel,equipment=t.equipment,strength=round(t.strength_ratio,3),weapon=p.get("weapon"))
            if el.count==0:self.log("ELEMENT_DISABLED",unit=t.uid,element=el.eid,role=el.role)
            if t.current_strength<=0:
                t.state=UnitState.DESTROYED; self.log("DESTROYED",unit=t.uid,source=p.get("source"))
                self._on_unit_destroyed(t,p.get("source"))
            else:self._check_reactive_branches(t)




    def _check_reactive_branches(self,u):
        if self._update_crew_readiness(u):
            return
        # Damage events may trigger a branch immediately rather than waiting for the next unit step.
        self._try_condition_branch(u)

    def _try_condition_branch(self, u: Unit) -> bool:
        """Replace the active order with its on_true branch when all conditions hold."""
        o=u.current_order
        if not o or not o.conditions or not o.on_true:
            return False
        if o.start_at_s is not None and self.time<o.start_at_s:
            return False    # a gated phase order is not active yet
        if not ConditionEvaluator.eval_all(self,u,o.conditions):
            return False
        branched=compile_order_fragment(self,u,o.on_true)
        self.log("CONDITION_BRANCH",unit=u.uid,from_order=o.order_id,to_order=branched.kind,phase=o.phase_id)
        self.reset_order_execution_state(u)
        u.current_order=branched
        return True

    def save_log(self,path):
        with open(path,"w",encoding="utf-8") as f:
            for rec in self.logs:f.write(json.dumps(rec,ensure_ascii=False)+"\n")
