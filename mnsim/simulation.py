from __future__ import annotations
from .mobility import movement_speed_mps
from typing import Dict
import json, math, random
from .model import Unit, UnitState, Order, Track
from .events import EventQueue
from .bml import apply_branch, compile_order_fragment, ConditionEvaluator
from .combat import CombatResolver
from .doctrine import DoctrineEngine
from .indirect_fire import IndirectFireResolver
from .belief import ContactBeliefPolicy
from .fire_control import FireControlEngine
from .terrain import TerrainModel
from .damage import DamageResolver
from .environment import EnvironmentObservationModel
from .communications import CommunicationNetwork
from .mounted import (initialize_transport_metadata, dismount_organic, mount_organic, board_external,
                      disembark_external, free_seats, carrier_operable)

from .perception import PerceptionMixin
from .composition import CompositionMixin


class Simulation(PerceptionMixin, CompositionMixin):
    def __init__(self, seed=7):
        self.time = 0.0
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
        self.units[unit.uid] = unit
        self.log("UNIT_ADD", unit=unit.uid, side=unit.side.value, pos=unit.pos,
                 personnel=unit.personnel, equipment=unit.equipment)

    def log(self, kind: str, **data): self.logs.append({"t": round(self.time,3), "kind":kind, **data})

    def issue_order(self, uid: str, order: Order):
        self.units[uid].order_queue.append(order); self.log("ORDER_ISSUED",unit=uid,order=order.kind,order_id=order.order_id)

    def tick(self, dt: float):
        if self.paused: return
        self.step(dt*self.speed)

    def step(self, sim_dt: float):
        """Advance simulation time by ``sim_dt`` seconds (not scaled by ``speed``)."""
        sim_dt=float(sim_dt)
        if not math.isfinite(sim_dt) or sim_dt<0.0:
            raise ValueError(f"simulation step must be finite and non-negative: {sim_dt!r}")
        start=self.time; end=start+sim_dt
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
        if self.paused or wall_dt <= 0.0:
            return
        fixed=max(float(max_sim_step_s),0.01)
        speed=max(float(self.speed),1e-9)
        # Cap one call's backlog so a stalled frame cannot trigger a long catch-up freeze.
        budget=min(float(wall_dt)*speed, fixed*int(self.combat_config.get("max_steps_per_frame",256)))
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

    def _step_unit(self,u:Unit,dt:float):
        # "No route" describes the movement attempted in *this* step.  A flag left by an earlier
        # doctrine withdrawal must not make a later HOLD/DEFEND look unreachable.
        u.metadata.pop("_nav_no_path",None)
        if self._update_crew_readiness(u):
            return
        if u.current_order is None and u.order_queue:
            u.current_order=u.order_queue.pop(0)
            u.metadata.pop("autonomous_fallback_reason",None)
            u.metadata.pop("unreachable_fallback_withdraw_dest",None)
            self.log("ORDER_START",unit=u.uid,order=u.current_order.kind,order_id=u.current_order.order_id)
        o=u.current_order
        if o is None:
            # A formation whose mission was abandoned because terrain became unreachable
            # remains tactically autonomous rather than oscillating between IDLE and replanning.
            if u.metadata.get("autonomous_fallback_reason"):
                if self.doctrine.autonomous_fallback(u,dt):
                    return
                u.state=UnitState.DEFENDING
                u.metadata["tactical_reason"]=str(u.metadata.get("autonomous_fallback_reason"))
                return
            u.state=UnitState.IDLE; return

        # Absolute scenario-time gate used by phase/timed BML.  Waiting never teleports or
        # accelerates a formation; normal local doctrine may still react to a real contact.
        if o.start_at_s is not None and self.time < o.start_at_s:
            if self._doctrine_override(u,dt):
                return
            u.state=UnitState.DEFENDING
            phase=f" / {o.phase_id}" if o.phase_id else ""
            u.metadata["tactical_reason"]=f"WAITING FOR SCHEDULED ORDER{phase} @ T={o.start_at_s:.1f}"
            return

        # Start-time for duration/condition accounting begins when execution is actually permitted,
        # not when a future phase order first becomes the head of the queue.
        u.metadata.setdefault("order_started_t",self.time)

        # Active conditions are reactive triggers, not preconditions.  Evaluate every simulation
        # step so time-, position-, capability-, and loss-based branches do not depend on taking
        # a damage event.  This also makes the already-present conditional BML semantics complete.
        if self._try_condition_branch(u):
            return

        # A deadline is a command constraint, not a physics override. Missing it is logged; an
        # explicit on_deadline branch may change the plan. Otherwise the formation keeps trying.
        if o.deadline_s is not None and self.time >= o.deadline_s:
            deadline_key=f"_deadline_logged:{o.order_id}"
            if not u.metadata.get(deadline_key):
                u.metadata[deadline_key]=True
                self.log("ORDER_DEADLINE_MISSED",unit=u.uid,order=o.kind,order_id=o.order_id,deadline_s=o.deadline_s,phase=o.phase_id)
            if o.on_deadline:
                branched=compile_order_fragment(self,u,o.on_deadline)
                self.log("DEADLINE_BRANCH",unit=u.uid,from_order=o.order_id,to_order=branched.kind,phase=o.phase_id)
                self.reset_order_execution_state(u)
                u.current_order=branched
                return

        if self._doctrine_override(u,dt):
            return
        if o.kind in ("DISMOUNT","MOUNT","BOARD","DISEMBARK"):
            self._step_mounted_order(u,o,dt)
        elif o.kind in ("ENTER_BUILDING","EXIT_BUILDING"):
            self._step_building_movement_order(u,o,dt)
        elif o.kind in ("MOVE","ATTACK","RETREAT"):
            dest=tuple(o.params.get("destination",u.pos)); u.metadata["objective"]=dest
            if o.kind == "ATTACK":
                self._step_attack_order(u, o, dest, dt)
            else:
                u.state=UnitState.RETREATING if o.kind=="RETREAT" else UnitState.MOVING
                arrived=self._move_toward(u,dest,dt)
                if arrived:self._complete_order(u)
        elif o.kind in ("ATTACK_UNIT","DESTROY_UNIT"):
            self._step_entity_attack_order(u,o,dt)
        elif o.kind=="ATTACK_STRUCTURE":
            self._step_structure_attack_order(u,o,dt)
        elif o.kind=="BUILD_BARRICADE":
            self._step_build_barricade_order(u,o,dt)
        elif o.kind == "DEFEND_AREA":
            self._step_defend_area_order(u,o,dt)
        elif o.kind in ("DEFEND","HOLD"):
            u.state=UnitState.DEFENDING; duration=float(o.params.get("duration_s",-1)); started=u.metadata.setdefault("order_started_t",self.time)
            if duration>=0 and self.time-started>=duration:self._complete_order(u)
        elif o.kind=="STRIKE_INFRASTRUCTURE":
            # Execution is handled by the artillery fire-control pass so it uses the same FDC,
            # preparation, reload, CEP and time-of-flight pipeline as other indirect fires.
            u.state=UnitState.DEFENDING
            targets=list(o.params.get("targets",[]))
            idx=self._advance_infrastructure_index(u,targets,log_completed=False)
            if idx>=len(targets):
                u.metadata.pop("infrastructure_strike_index",None); self._complete_order(u)
        elif o.kind=="WAIT":
            duration=float(o.params.get("duration_s",10)); started=u.metadata.setdefault("order_started_t",self.time)
            if self.time-started>=duration:self._complete_order(u)
        else: self.log("ORDER_UNKNOWN",unit=u.uid,order=o.kind); self._complete_order(u)

        if u.current_order is not None and u.metadata.get("_nav_no_path"):
            self._handle_unreachable_order(u)


    def _step_building_movement_order(self,u:Unit,o:Order,dt:float):
        """Execute explicit building boundary crossing.

        Ordinary MOVE/ATTACK/RETREAT never receive building access. ENTER/EXIT temporarily grant
        permission for exactly one building footprint, keeping building traversal intentional and
        BML-visible while leaving occupancy-size policy to the command-generation layer.
        """
        if not self.terrain:
            self._complete_order(u); return
        bid=str(o.params.get("target_structure","")); b=self.terrain.building_by_id(bid)
        if b is None or not self.terrain.building_operational(b):
            self.log("BUILDING_MOVE_FAILED",unit=u.uid,order=o.kind,building=bid,reason="INVALID_BUILDING")
            self._complete_order(u); return
        mobility=str(u.unit_type.metadata.get("mobility_class","FOOT")).upper()
        if mobility!="FOOT":
            self.log("BUILDING_MOVE_FAILED",unit=u.uid,order=o.kind,building=bid,reason="NON_FOOT_MOBILITY")
            self._complete_order(u); return
        u.metadata["_building_access_id"]=bid
        dest=tuple(o.params.get("destination",u.pos)); u.metadata["objective"]=dest
        if o.kind=="ENTER_BUILDING":
            if self.terrain.building_at(dest,include_destroyed=True) is not b:
                dest=tuple(self.terrain.building_center(b)); o.params["destination"]=list(dest); u.metadata["objective"]=dest
            u.state=UnitState.MOVING; u.metadata["tactical_reason"]=f"ENTER BUILDING {bid}"
            if self._move_toward(u,dest,dt):
                u.metadata["occupied_building_id"]=bid
                self._complete_order(u)
        else:
            u.state=UnitState.MOVING; u.metadata["tactical_reason"]=f"EXIT BUILDING {bid}"
            if self._move_toward(u,dest,dt):
                u.metadata.pop("occupied_building_id",None)
                self._complete_order(u)

    def _step_mounted_order(self,u:Unit,o:Order,dt:float):
        """Generic mounted/dismounted transport behavior driven by platform capacity metadata."""
        kind=o.kind.upper(); initialize_transport_metadata(u)
        if kind=="DISMOUNT":
            dest=o.params.get("destination")
            if dest is not None and math.dist(u.pos,tuple(dest))>float(u.metadata.get("embark_radius_m",20.0)):
                u.state=UnitState.MOVING; u.metadata["tactical_reason"]="MOVE TO DISMOUNT POINT"
                self._move_toward(u,tuple(dest),dt); return
            u.state=UnitState.DISMOUNTING; started=u.metadata.setdefault("mounted_action_started_t",self.time)
            if self.time-started < float(u.metadata.get("dismount_time_s",15.0)):
                u.metadata["tactical_reason"]="DISMOUNTING ORGANIC INFANTRY"; return
            dismount_organic(self,u); u.metadata.pop("mounted_action_started_t",None); self._complete_order(u); return
        if kind=="MOUNT":
            cid=u.metadata.get("dismount_child_id"); child=self.units.get(cid) if cid else None
            if child is None or not child.alive:
                self._complete_order(u); return
            radius=float(u.metadata.get("embark_radius_m",20.0))
            if math.dist(u.pos,child.pos)>radius:
                u.state=UnitState.MOVING; u.metadata["tactical_reason"]="RENDEZVOUS WITH ORGANIC DISMOUNTS"
                self._move_toward(u,tuple(child.pos),dt); return
            u.state=UnitState.MOUNTING; started=u.metadata.setdefault("mounted_action_started_t",self.time)
            if self.time-started < float(u.metadata.get("embark_time_s",20.0)):
                u.metadata["tactical_reason"]="MOUNTING ORGANIC INFANTRY"; return
            mount_organic(self,u); u.metadata.pop("mounted_action_started_t",None); self._complete_order(u); return
        if kind=="BOARD":
            carrier=self.units.get(str(o.params.get("carrier","")))
            if carrier is None or not carrier.alive or carrier.side!=u.side:
                self.log("BOARD_FAILED",unit=u.uid,reason="INVALID_CARRIER"); self._complete_order(u); return
            initialize_transport_metadata(carrier)
            if not carrier_operable(carrier):
                u.state=UnitState.DEFENDING; u.metadata["tactical_reason"]="BOARD / CARRIER INOPERABLE (CREW SHORTAGE)"; return
            if free_seats(self,carrier)<max(1,u.personnel):
                u.state=UnitState.DEFENDING; u.metadata["tactical_reason"]="BOARD / INSUFFICIENT EMPTY SEATS"; return
            radius=float(carrier.metadata.get("embark_radius_m",20.0))
            if math.dist(u.pos,carrier.pos)>radius:
                u.state=UnitState.MOVING; u.metadata["tactical_reason"]="BOARD / MOVE TO FRIENDLY CARRIER"
                self._move_toward(u,tuple(carrier.pos),dt); return
            u.state=UnitState.MOUNTING; started=u.metadata.setdefault("mounted_action_started_t",self.time)
            if self.time-started < float(carrier.metadata.get("embark_time_s",20.0)):
                u.metadata["tactical_reason"]="BOARDING FRIENDLY TRANSPORT"; return
            if board_external(self,u,carrier):
                u.metadata.pop("mounted_action_started_t",None); self._complete_order(u)
            return
        if kind=="DISEMBARK":
            u.state=UnitState.DISMOUNTING; started=u.metadata.setdefault("mounted_action_started_t",self.time)
            if self.time-started < float(u.metadata.get("dismount_time_s",15.0)):
                u.metadata["tactical_reason"]="DISEMBARKING PASSENGERS"; return
            disembark_external(self,u,str(o.params.get("passenger","ALL")))
            u.metadata.pop("mounted_action_started_t",None); self._complete_order(u); return

    def _best_attack_track(self, u: Unit, include_lost: bool = False):
        """Best hostile contact known to this unit, using only its FoW track database."""
        memory=float(self.combat_config.get("attack_track_memory_s",90.0))
        candidates=[]
        for tid,tr in u.local_tracks.items():
            tgt=self.units.get(tid)
            if not tgt or tgt.side==u.side or tr.state=="DESTROYED":
                continue
            age=self.time-tr.last_seen_time
            if age>memory:
                continue
            actionable=self._track_for(u,tgt) is not None
            if not actionable and not include_lost:
                continue
            # Prefer actionable, fresh, confident contacts. No ground-truth position is used.
            score=(1 if actionable else 0, tr.confidence, -age)
            candidates.append((score,tgt,tr))
        if not candidates:
            return None
        candidates.sort(key=lambda x:x[0],reverse=True)
        return candidates[0][1],candidates[0][2],bool(candidates[0][0][0])

    def _doctrine_override(self, u: Unit, dt: float) -> bool:
        """Compatibility delegate. Tactical behavior lives in mnsim/doctrine.py."""
        return self.doctrine.step(u, dt)

    @staticmethod
    def _point_in_polygon(point, polygon):
        from .terrain import _point_in_poly
        pts=[tuple(p) for p in polygon]
        return len(pts)>=3 and _point_in_poly(tuple(point),pts)

    def _inside_defend_area(self,u:Unit,o:Order):
        p=o.params
        if p.get("polygon"):
            return self._point_in_polygon(u.pos,p["polygon"])
        center=tuple(p.get("center",u.pos)); radius=float(p.get("radius_m",120.0))
        return math.dist(u.pos,center)<=radius

    def _step_structure_attack_order(self,u:Unit,o:Order,dt:float):
        sid=str(o.params.get("target_structure",""));b=self.terrain.building_by_id(sid) if self.terrain else None
        if b is None or not self.terrain.building_operational(b):
            self._complete_order(u);return
        aim=self.terrain.building_center(b);capable=[]
        for el,w in u.operational_weapons():
            if str(w.capability).upper()=="INDIRECT_FIRE":continue
            if "STRUCTURE" in {str(x).upper() for x in w.target_tags} or bool(w.metadata.get("structure_capable",False)):
                capable.append((el,w))
        if not capable:
            u.state=UnitState.DEFENDING;u.metadata["tactical_reason"]="ATTACK_STRUCTURE / NO STRUCTURE-CAPABLE DIRECT WEAPON";return
        maxr=max(w.range_m for el,w in capable);d=math.dist(u.pos,aim)
        lane=self.terrain.direct_fire_modifier(u.pos,aim)
        if d>maxr or not bool(lane.get("allowed",True)):
            u.state=UnitState.MOVING;u.metadata["tactical_reason"]="ATTACK_STRUCTURE / MANEUVER TO FIRING POSITION"
            self._move_toward(u,aim,dt);return
        u.state=UnitState.ENGAGING;u.metadata["tactical_reason"]="ATTACK_STRUCTURE / ENGAGING BUILDING"
        for el,w in capable:self.combat.fire_structure_weapon(u,b,el,w)

    def _step_build_barricade_order(self,u:Unit,o:Order,dt:float):
        """Construct one 10 m MIL1-class defensive barrier at an explicit BML position.

        Default capacity is zero.  A positive per-unit ``barricade_limit`` represents preallocated
        barrier material/material-handling support; no permanent engineer sub-element is invented.
        """
        branch=str(u.branch).upper(); mobility=str(u.unit_type.metadata.get("mobility_class","FOOT")).upper()
        builders={str(x).upper() for x in self.combat_config.get("barricade_builder_branches",["INFANTRY","RECON","SPECIAL_OPERATIONS"])}
        caps={str(x).upper() for x in u.unit_type.metadata.get("engineering_capabilities",[])}
        if ("BARRICADE" not in caps and branch not in builders) or mobility!="FOOT":
            u.metadata["tactical_reason"]="BUILD BARRICADE / INFANTRY ONLY";self.log("BARRICADE_BUILD_REJECTED",unit=u.uid,reason="INFANTRY_ONLY");self._complete_order(u);return
        limit=max(0,int(u.metadata.get("barricade_limit",0))); built=max(0,int(u.metadata.get("barricades_built",0)))
        if built>=limit:
            u.metadata["tactical_reason"]="BUILD BARRICADE / CAPACITY EXHAUSTED";self.log("BARRICADE_BUILD_REJECTED",unit=u.uid,reason="CAPACITY",limit=limit,built=built);self._complete_order(u);return
        pos=tuple(o.params.get("position",u.pos));u.metadata["objective"]=pos
        if math.dist(u.pos,pos)>5.0:
            u.state=UnitState.MOVING;u.metadata["tactical_reason"]="BUILD BARRICADE / MOVE TO SITE";self._move_toward(u,pos,dt);return
        u.state=UnitState.DEFENDING
        started=u.metadata.setdefault("barricade_build_started_t",self.time)
        duration=max(1.0,float(o.params.get("construction_time_s",1200.0)))
        u.metadata["tactical_reason"]=f"BUILD BARRICADE / CONSTRUCTING {max(0.0,duration-(self.time-started)):.0f}s"
        if self.time-started<duration:return
        wall=self.terrain.add_barricade(pos,float(o.params.get("heading_deg",0.0)),builder_uid=u.uid)
        u.metadata["barricades_built"]=built+1;u.metadata.pop("barricade_build_started_t",None)
        self.log("BARRICADE_BUILT",unit=u.uid,barricade=wall.get("id"),center=wall.get("center"),heading_deg=wall.get("heading_deg"),length_m=wall.get("length_m"))
        self._complete_order(u)

    def _step_defend_area_order(self,u:Unit,o:Order,dt:float):
        """Occupy a bounded area and optionally manoeuvre against contacts without abandoning it.

        This is the local-security counterpart to HOLD: a defender may close on an actionable Track
        inside the protected area, but a fleeing enemy cannot lure it beyond the BML boundary.
        """
        p=o.params
        center=tuple(p.get("center",u.pos))
        if p.get("polygon") and not p.get("center"):
            poly=[tuple(x) for x in p["polygon"]]
            center=(sum(x for x,_ in poly)/len(poly),sum(y for _,y in poly)/len(poly))
        if not self._inside_defend_area(u,o):
            u.state=UnitState.MOVING
            u.metadata["tactical_reason"]="RETURN TO DEFEND AREA"
            self._move_toward(u,center,dt)
            return

        # Only perceived Track coordinates are considered.  The defended-area boundary remains a
        # hard leash for pursuit; direct fire itself may reach beyond the boundary if a valid LOS
        # and weapon solution already exist.
        if bool(p.get("react_to_contacts",True)):
            contact=self._best_attack_track(u,include_lost=False)
            if contact is not None:
                tgt,tr,actionable=contact
                est=tuple(tr.estimated_pos)
                within_contact_zone=True
                if p.get("polygon"):
                    within_contact_zone=self._point_in_polygon(est,p["polygon"])
                else:
                    engage_r=float(p.get("engagement_radius_m",p.get("radius_m",120.0)))
                    within_contact_zone=math.dist(center,est)<=engage_r
                if within_contact_zone and actionable:
                    can_fire=self._unit_can_affect(u,tgt)
                    close_more=self.doctrine.should_close_for_direct_fire(u,tgt,tr) if can_fire else False
                    if can_fire and not close_more:
                        u.state=UnitState.ENGAGING
                        u.metadata["tactical_reason"]="DEFEND AREA / ENGAGE CONTACT"
                        return
                    if bool(p.get("pursue_within_area",False)):
                        pursuit_ok=True
                        if p.get("polygon"):
                            pursuit_ok=self._point_in_polygon(est,p["polygon"])
                        else:
                            pursuit_r=float(p.get("pursuit_radius_m",p.get("radius_m",120.0)))
                            pursuit_ok=math.dist(center,est)<=pursuit_r
                        if pursuit_ok:
                            u.state=UnitState.MOVING
                            u.metadata["tactical_reason"]="DEFEND AREA / BOUNDED PURSUIT"
                            self._move_toward(u,est,dt)
                            return

        if bool(p.get("return_to_center",False)):
            anchor_r=float(p.get("anchor_radius_m",max(15.0,float(p.get("radius_m",120.0))*0.20)))
            if math.dist(u.pos,center)>anchor_r:
                u.state=UnitState.MOVING
                u.metadata["tactical_reason"]="DEFEND AREA / RE-CENTER"
                self._move_toward(u,center,dt)
                return
        u.state=UnitState.DEFENDING
        u.metadata["tactical_reason"]="DEFEND AREA / HOLD BOUNDARY"
        duration=float(p.get("duration_s",-1))
        started=u.metadata.setdefault("order_started_t",self.time)
        if duration>=0 and self.time-started>=duration:self._complete_order(u)

    def _specific_target_track(self,u:Unit,target_uid:str,include_lost=True):
        tgt=self.units.get(target_uid)
        tr=u.local_tracks.get(target_uid)
        if not tgt or not tr or tr.state=="DESTROYED":return None
        age=self.time-tr.last_seen_time
        memory=float(self.combat_config.get("attack_track_memory_s",90.0))
        if age>memory:return None
        actionable=self._track_for(u,tgt) is not None
        if not actionable and not include_lost:return None
        return tgt,tr,actionable

    def _step_entity_attack_order(self,u:Unit,o:Order,dt:float):
        """Mission-oriented attack using perceived target locations, never live enemy position for pursuit."""
        target_uid=str(o.params.get("target_unit",""))
        tgt=self.units.get(target_uid)
        if tgt is None:
            self.log("BML_TARGET_MISSING",unit=u.uid,target=target_uid,order=o.kind)
            self._complete_order(u); return
        known=u.local_tracks.get(target_uid)
        if known is not None and known.state=="DESTROYED":
            # Completion requires battle-damage information (own observation or a report),
            # never the live ``alive`` flag of the target.
            self.log("BML_TARGET_DESTROYED",unit=u.uid,target=target_uid,order=o.kind,
                     bda_source=getattr(known,"source",None))
            self._complete_order(u); return
        if not tgt.active and tgt.state!=UnitState.DESTROYED:
            # Administrative identity change (aggregated into a parent, emptied by detachment,
            # embarked): the commanded entity no longer exists as an addressable formation.
            self.log("BML_TARGET_MISSING",unit=u.uid,target=target_uid,order=o.kind,reason="NOT_ADDRESSABLE")
            self._complete_order(u); return

        # Explicit mission target gets priority when it is perceived.
        contact=self._specific_target_track(u,target_uid,include_lost=True)
        if contact:
            u.metadata.pop("_target_search_since",None)
            _,tr,actionable=contact
            u.metadata["last_contact_id"]=target_uid
            u.metadata["last_contact_pos"]=tuple(tr.estimated_pos)
            u.metadata["last_contact_t"]=tr.last_seen_time
            if actionable:
                can_fire=self._unit_can_affect(u,tgt)
                close_more=self.doctrine.should_close_for_direct_fire(u,tgt,tr)
                if can_fire and not close_more:
                    u.state=UnitState.ENGAGING
                    u.metadata["tactical_reason"]=f"{o.kind} / DOCTRINAL ENGAGEMENT RANGE"
                    return
                u.state=UnitState.ATTACKING
                if can_fire:
                    u.metadata["tactical_reason"]=f"{o.kind} / FIRE-AND-CLOSE TO COMBINED-ARMS RANGE"
                else:
                    u.metadata["tactical_reason"]=f"{o.kind} / MANEUVER TO FIRE / NO CURRENT FIRING LANE"
                self._move_toward(u,tuple(tr.estimated_pos),dt); return
            u.state=UnitState.SEARCHING
            u.metadata["tactical_reason"]=f"{o.kind} / SEARCH TARGET LAST KNOWN"
            self._move_toward(u,tuple(tr.estimated_pos),dt); return

        # No usable track on the mission target.  A search that finds nothing within the timeout
        # ends the mission (target not found) instead of waiting forever for an unobserved kill.
        since=u.metadata.setdefault("_target_search_since",self.time)
        timeout=float(o.params.get("search_timeout_s",self.combat_config.get("entity_attack_search_timeout_s",600.0)))
        if timeout>=0 and self.time-since>=timeout:
            self.log("BML_TARGET_NOT_FOUND",unit=u.uid,target=target_uid,order=o.kind,searched_s=round(self.time-since,1))
            self._complete_order(u); return

        # En route, fight actionable intervening contacts rather than ignoring them.
        other=self._best_attack_track(u,include_lost=False)
        if other:
            otgt,otr,_=other
            can_fire=self._unit_can_affect(u,otgt)
            close_more=self.doctrine.should_close_for_direct_fire(u,otgt,otr)
            if can_fire and not close_more:
                u.state=UnitState.ENGAGING
                u.metadata["tactical_reason"]=f"{o.kind} / ENGAGE INTERVENING CONTACT"
                return
            u.state=UnitState.ATTACKING
            if can_fire:
                u.metadata["tactical_reason"]=f"{o.kind} / FIRE-AND-CLOSE ON INTERVENING CONTACT"
            else:
                u.metadata["tactical_reason"]=f"{o.kind} / MANEUVER TO FIRE ON INTERVENING CONTACT"
            self._move_toward(u,tuple(otr.estimated_pos),dt); return

        # No current/lost Track for this specific target. Target identity alone must not reveal
        # Ground Truth position. Only an explicit BML-provided intelligence/search reference can
        # authorize movement toward an unobserved entity. Otherwise remain in place awaiting
        # acquisition/shared Track rather than magically navigating to sim.units[target].pos.
        ref=o.params.get("search_reference")
        if ref is None:
            u.state=UnitState.SEARCHING
            u.metadata.pop("objective",None)
            u.metadata["tactical_reason"]=f"{o.kind} / TARGET LOCATION UNKNOWN / AWAIT TRACK"
            return

        objective=tuple(ref)
        u.state=UnitState.ATTACKING
        u.metadata["objective"]=objective
        u.metadata["tactical_reason"]=f"{o.kind} / ADVANCE TO EXPLICIT TARGET REFERENCE"
        arrived=self._move_toward(u,objective,dt)
        if arrived:
            u.state=UnitState.SEARCHING
            u.metadata["tactical_reason"]=f"{o.kind} / SEARCHING EXPLICIT TARGET AREA"

    def _step_attack_order(self, u: Unit, o: Order, objective, dt: float):
        """Persistent attack behavior: advance, engage, search last-known position, then continue mission."""
        persistent=bool(o.params.get("persistent",True))
        contact=self._best_attack_track(u,include_lost=True)
        if contact:
            tgt,tr,actionable=contact
            u.metadata["last_contact_id"]=tgt.uid
            u.metadata["last_contact_pos"]=tuple(tr.estimated_pos)
            u.metadata["last_contact_t"]=tr.last_seen_time
            if actionable:
                # Hold while a currently known target is inside any effective direct-fire envelope;
                # otherwise close on the perceived position, not on ground truth.
                can_fire=self._unit_can_affect(u,tgt)
                close_more=self.doctrine.should_close_for_direct_fire(u,tgt,tr)
                if can_fire and not close_more:
                    u.state=UnitState.ENGAGING
                    u.metadata["tactical_reason"]="ACTIONABLE TRACK / DOCTRINAL ENGAGEMENT RANGE"
                    return
                u.state=UnitState.ATTACKING
                if can_fire:
                    u.metadata["tactical_reason"]="CONTACT / FIRE-AND-CLOSE TO COMBINED-ARMS RANGE"
                else:
                    u.metadata["tactical_reason"]="CONTACT / MANEUVER TO FIRE / NO CURRENT FIRING LANE"
                self._move_toward(u,tuple(tr.estimated_pos),dt)
                return

            # Recently lost/stale contact: search the last known position before resuming advance.
            search_pos=tuple(tr.estimated_pos)
            u.state=UnitState.SEARCHING
            u.metadata["tactical_reason"]="TRACK LOST / SEARCH LAST KNOWN POSITION"
            arrived=self._move_toward(u,search_pos,dt)
            if arrived:
                since=u.metadata.setdefault("search_arrived_t",self.time)
                hold=float(o.params.get("search_hold_s",self.combat_config.get("attack_search_hold_s",12.0)))
                if self.time-since < hold:
                    return
                # Expire this remembered contact for maneuver purposes. The track object remains for AAR/UI.
                tr.last_seen_time=self.time-float(self.combat_config.get("attack_track_memory_s",90.0))-1.0
                u.metadata.pop("search_arrived_t",None)
        else:
            u.metadata.pop("search_arrived_t",None)

        # No usable contact: continue toward the assigned objective. ATTACK is persistent by default,
        # so arrival means secure/search the objective rather than silently becoming IDLE.
        u.state=UnitState.ATTACKING
        u.metadata["tactical_reason"]="ADVANCE TO OBJECTIVE"
        arrived=self._move_toward(u,objective,dt)
        if arrived:
            if persistent:
                u.state=UnitState.SEARCHING
                u.metadata["tactical_reason"]="OBJECTIVE REACHED / SEARCHING FOR CONTACT"
            else:
                self._complete_order(u)

    def _clear_navigation_state(self,u):
        for key in ("_nav_destination","_nav_bridge_signature","_nav_route","_nav_route_index",
                    "_nav_no_path","_nav_no_path_destination"):
            u.metadata.pop(key,None)

    def _handle_unreachable_order(self,u):
        """Abandon a movement mission that has no physically valid route.

        Infrastructure destruction or impassable terrain can invalidate a previously valid route.
        Such a failure is not treated as mission completion: it is logged as an order failure, the
        current order is discarded, and the formation enters an autonomous fallback state.  If a
        later queued mission exists it may still be attempted on the next tick.
        """
        o=u.current_order
        if o is None:return
        destination=u.metadata.get("_nav_no_path_destination",u.metadata.get("objective"))
        self.log("ORDER_UNREACHABLE",unit=u.uid,order=o.kind,order_id=o.order_id,destination=destination)
        u.metadata.setdefault("failed_orders",[]).append({"order_id":o.order_id,"kind":o.kind,"reason":"NO_ROUTE","destination":destination})
        u.current_order=None
        u.metadata.pop("order_started_t",None)
        u.metadata["autonomous_fallback_reason"]="NO ROUTE / AUTONOMOUS HOLD"
        self._clear_navigation_state(u)
        u.metadata.pop("_building_access_id",None)
        u.state=UnitState.DEFENDING
        u.metadata["tactical_reason"]="NO ROUTE / AUTONOMOUS HOLD"

    #: Per-order execution state that must not leak into the next order.
    ORDER_EXECUTION_KEYS=("order_started_t","search_arrived_t","objective","mounted_action_started_t",
                          "barricade_build_started_t","_building_access_id","_target_search_since",
                          "infrastructure_strike_index")

    def reset_order_execution_state(self, u: Unit):
        """Forget timers, routes and temporary permissions of the order being replaced."""
        for key in self.ORDER_EXECUTION_KEYS:
            u.metadata.pop(key,None)
        self._clear_navigation_state(u)

    def _complete_order(self,u):
        o=u.current_order
        self.log("ORDER_COMPLETE",unit=u.uid,order=o.kind,order_id=o.order_id)
        u.current_order=None
        self.reset_order_execution_state(u)
        apply_branch(self,u,o)

    def _handle_terrain_transition(self,u,old_pos,new_pos):
        """Apply irreversible composition effects caused by entering special terrain."""
        if not self.terrain:return
        old_lake=self.terrain.lake_at(old_pos) if hasattr(self.terrain,"lake_at") else None
        new_lake=self.terrain.lake_at(new_pos) if hasattr(self.terrain,"lake_at") else None
        if new_lake is not None and old_lake is None:
            mobility=str(u.unit_type.metadata.get("mobility_class","FOOT")).upper()
            if mobility=="FOOT":
                dropped=[]
                # Swimming infantry abandon crew-served/heavy and anti-armor weapons. Individual
                # rifles/DMRs/SAWs remain; this is deliberately explicit and data-driven by weapon
                # capability/UI family rather than weapon names.
                for el in u.elements.values():
                    if el.category.upper()!="PERSONNEL":continue
                    kept=[]
                    for w in el.weapons:
                        fam=str(w.metadata.get("ui_range_family","")).upper(); cap=str(w.capability).upper()
                        heavy=(cap=="ANTI_ARMOR" or fam in {"GUIDED_AT","LIGHT_AT"} or str(w.metadata.get("swim_incompatible","")).lower()=="true"
                               or (fam=="MACHINE_GUN" and str(w.metadata.get("ui_range_label","")).upper() in {"MG","GPMG","HMG"}))
                        if heavy:dropped.append(w.name)
                        else:kept.append(w)
                    el.weapons=kept
                if dropped:
                    u.metadata.setdefault("dropped_weapons",[]).extend(dropped)
                    self.log("SWIM_HEAVY_WEAPONS_DROPPED",unit=u.uid,weapons=dropped,lake=str(new_lake.get("id","LAKE")))
                u.metadata["swimming"]=True
        elif old_lake is not None and new_lake is None:
            u.metadata.pop("swimming",None)

    def _move_toward(self,u,dest,dt):
        # Terrain may redirect a non-amphibious formation to a bridge before the final destination.
        move_dest=self.terrain.movement_target(u,tuple(dest)) if self.terrain else tuple(dest)
        dx,dy=move_dest[0]-u.pos[0],move_dest[1]-u.pos[1]; d=math.hypot(dx,dy)
        arrival=float(self.combat_config.get("order_arrival_m",3.0))
        if d<1e-9:
            # Reaching a bridge waypoint is not the same as reaching the actual order destination.
            return math.dist(u.pos,tuple(dest))<=arrival
        # Tactical movement speed is data-driven by mobility class/terrain and current tactical state.
        # Unit max_speed_mps is a formation planning speed, not a vehicle brochure top speed.
        speed=movement_speed_mps(u,self.terrain,u.pos,move_dest)*self.stress.movement_factor(u)
        step=min(d,speed*dt)
        if d>0:
            candidate=(u.pos[0]+dx/d*step,u.pos[1]+dy/d*step)
            if self.terrain and not self.terrain.segment_passable(u,u.pos,candidate):
                # A valid endpoint does not imply a valid swept movement leg.
                # Discard stale routes so the next update can find an alternative.
                self._clear_navigation_state(u)
                u.metadata["tactical_reason"]="TERRAIN BLOCKED / REPLANNING"
                return False
            old_pos=u.pos
            self._handle_terrain_transition(u,old_pos,candidate)
            u.pos=candidate
            u.metadata["_moved_at"]=self.time
            u.heading_deg=math.degrees(math.atan2(dy,dx))
        return math.dist(u.pos,tuple(dest))<=arrival

    def _resolve_collapsed_bridges(self):
        """Detect bridges that became non-operational by any path (impact, script, editor)."""
        if not self.terrain or not self.terrain.bridges:
            return
        known=getattr(self,"_bridge_state",None)
        state={str(b.get("id")):self.terrain.bridge_operational(b) for b in self.terrain.bridges}
        self._bridge_state=state
        # First call: a bridge already down means any formation placed on its deck is stranded.
        known=known if known is not None else {}
        for bid,ok in state.items():
            if known.get(bid,True) and not ok:
                self._handle_destroyed_bridge(bid)

    def _handle_destroyed_bridge(self, bridge_id: str, source: str = ""):
        """Resolve formations standing on a bridge deck when it collapses.

        Without this a unit on the deck is left on water it cannot traverse and every movement
        leg out is rejected forever.  The formation is displaced to the nearest passable bank
        point along the bridge axis (the formation-scale abstraction of units crowding the
        approaches); casualties from the collapse itself are not invented here.
        """
        br=self.terrain.bridge_by_id(bridge_id) if self.terrain else None
        if br is None:
            return
        pts=self.terrain.bridge_points(br)
        if len(pts)<2:
            return
        def outward(p0,p1):
            dx,dy=p0[0]-p1[0],p0[1]-p1[1]; d=max(math.hypot(dx,dy),1e-9); return dx/d,dy/d
        ends=[(pts[0],outward(pts[0],pts[1])),(pts[-1],outward(pts[-1],pts[-2]))]
        for u in list(self.units.values()):
            if not u.alive or self.terrain._distance_to_bridge_deck(u.pos,br)>1e-6:
                continue
            if self.terrain.passable(u,u.pos):
                continue   # amphibious/ford-capable formations simply stay
            best=None
            for end,(ux,uy) in ends:
                for step in range(0,121,4):
                    cand=(end[0]+ux*step,end[1]+uy*step)
                    if self.terrain.passable(u,cand):
                        d=math.dist(u.pos,cand)
                        if best is None or d<best[0]:
                            best=(d,cand)
                        break
            if best is None:
                self.log("BRIDGE_COLLAPSE_UNIT_STRANDED",unit=u.uid,bridge=bridge_id,source=source)
                continue
            old=u.pos; u.pos=best[1]
            self._clear_navigation_state(u)
            self.log("BRIDGE_COLLAPSE_UNIT_DISPLACED",unit=u.uid,bridge=bridge_id,source=source,
                     from_pos=[round(old[0],1),round(old[1],1)],to_pos=[round(u.pos[0],1),round(u.pos[1],1)])

    def _handle_destroyed_building(self,building,source="",weapon=""):
        if not building or not bool(building.get("destroyed",False)):return
        bid=str(building.get("id","BUILDING"))
        if building.get("_occupant_kill_resolved"):return
        building["_occupant_kill_resolved"]=True
        for u in list(self.units.values()):
            if not u.alive:continue
            if self.terrain.building_at(u.pos,include_destroyed=True) is building:
                for el in u.elements.values():
                    el.count=0
                    if el.category.upper()=="EQUIPMENT":el.item_states=["DESTROYED"]*max(el.initial_count,len(el.item_states))
                u.state=UnitState.DESTROYED;u.active=False
                self.log("BUILDING_COLLAPSE_UNIT_DESTROYED",building=bid,unit=u.uid,source=source,weapon=weapon)
                self._on_unit_destroyed(u,source or None)





    # Public, UI-facing read-only queries (frontends must not call underscored engine internals).


    # ---------- local N:M engagement, now perception-driven ----------
    def _direct_units(self): return [u for u in self.units.values() if u.alive and not u.metadata.get("noncombat_proxy")]

    @staticmethod
    def _has_direct_weapon(unit):
        return any(w.capability.upper() != "INDIRECT_FIRE" for _,w in unit.operational_weapons())

    @staticmethod
    def _has_indirect_weapon(unit):
        return any(w.capability.upper() == "INDIRECT_FIRE" for e in unit.elements.values() for w in e.weapons)

    def _indirect_candidates(self, shooter, mode):
        candidates=[]
        for uid in shooter.local_tracks:
            target=self.units.get(uid)
            if target is None or target.side==shooter.side or target.metadata.get("noncombat_proxy"):
                continue
            if mode=="COUNTER_BATTERY" and shooter.local_tracks[uid].classification.upper()!="ARTILLERY":
                continue
            if self._unit_can_affect(shooter,target,mode):
                candidates.append(target)
        return candidates

    def _build_engagements(self):
        units=self._direct_units(); link=float(self.combat_config["engagement_link_m"]); adj={u.uid:set() for u in units}
        has_direct={u.uid:self._has_direct_weapon(u) for u in units}
        # Hostile edges require at least one side to possess an actionable contact; no omniscient proximity trigger.
        for i,a in enumerate(units):
            for b in units[i+1:]:
                if a.side==b.side: continue
                ta=self._track_for(a,b); tb=self._track_for(b,a)
                # A local engagement exists whenever at least one side has a usable track and
                # either side can actually affect the other from its perceived weapon envelope.
                # This avoids the old fixed-420 m cutoff prematurely breaking tank/direct-fire fights.
                can_a = ta is not None and self._unit_can_affect(a,b)
                can_b = tb is not None and self._unit_can_affect(b,a)
                # Keep a short contact-link fallback for close encounters where classification/capability
                # prevents fire (e.g. rifle infantry facing armor after its AT specialist is lost).
                close_contact=(ta is not None or tb is not None) and a.distance_to(b)<=link and (has_direct[a.uid] or has_direct[b.uid])
                if can_a or can_b or close_contact:
                    adj[a.uid].add(b.uid); adj[b.uid].add(a.uid)
        # Nearby friendlies may join the same local engagement component, but still fire only on their own tracks.
        for a in units:
            if not adj[a.uid]: continue
            for b in units:
                if a.uid!=b.uid and a.side==b.side and a.distance_to(b)<=link*0.75:
                    adj[a.uid].add(b.uid); adj[b.uid].add(a.uid)
        seen=set(); groups=[]; by_id={u.uid:u for u in units}
        for u in units:
            if u.uid in seen or not adj[u.uid]: continue
            stack=[u.uid]; ids=[]
            while stack:
                cur=stack.pop()
                if cur in seen: continue
                # Sorted expansion: set iteration order depends on PYTHONHASHSEED, and the member
                # order decides who fires first and therefore the RNG draw sequence.
                seen.add(cur); ids.append(cur); stack.extend(sorted(adj[cur]-seen))
            members=[by_id[x] for x in ids]
            if len({m.side for m in members})>=2: groups.append(members)
        return groups

    def _unit_can_affect(self,shooter:Unit,target:Unit,mode="DIRECT"):
        return self.combat.unit_can_affect(shooter,target,mode)

    def _select_target(self,shooter,enemies,mode="DIRECT"):
        return self.combat.select_target(shooter,enemies,mode)

    def _infrastructure_target_alive(self, target_id: str) -> bool:
        if not self.terrain:
            return False
        br=self.terrain.bridge_by_id(target_id)
        bld=self.terrain.building_by_id(target_id)
        return ((br is not None and self.terrain.bridge_operational(br))
                or (bld is not None and self.terrain.building_operational(bld)))

    def _advance_infrastructure_index(self, u: Unit, targets, log_completed: bool) -> int:
        """Skip already-destroyed targets; return the index of the next live target."""
        idx=int(u.metadata.get("infrastructure_strike_index",0))
        while idx<len(targets) and not self._infrastructure_target_alive(str(targets[idx])):
            if log_completed:
                self.log("INFRASTRUCTURE_TARGET_COMPLETE",unit=u.uid,target=str(targets[idx]))
            idx+=1; u.metadata["infrastructure_strike_index"]=idx
        return idx

    def _execute_infrastructure_strike(self, arty:Unit) -> bool:
        o=arty.current_order
        if not o or o.kind!="STRIKE_INFRASTRUCTURE": return False
        # A scheduled strike cannot fire before its start time, nor while orders are suspended
        # for crew loss; the battery simply holds (and skips autonomous fire) meanwhile.
        if (o.start_at_s is not None and self.time<o.start_at_s) or arty.state==UnitState.COMBAT_INEFFECTIVE:
            return True
        targets=list(o.params.get("targets",[]))
        idx=self._advance_infrastructure_index(arty,targets,log_completed=True)
        if idx>=len(targets):return True
        bid=str(targets[idx]);br=self.terrain.bridge_by_id(bid);bld=self.terrain.building_by_id(bid) if hasattr(self.terrain,"building_by_id") else None
        aim=tuple(self.terrain.bridge_center(br)) if br is not None else tuple(self.terrain.building_center(bld));requested=False
        for el,w in arty.operational_weapons():
            if w.capability.upper()!="INDIRECT_FIRE": continue
            if math.dist(arty.pos,aim)>w.range_m: continue
            requested=self.fire_control.request_coordinate(arty,el,w,aim,f"INFRA:{bid}","INFRASTRUCTURE_STRIKE",float(o.params.get("coordinate_error_m",3.0))) or requested
        if requested:
            self.log("INFRASTRUCTURE_STRIKE_REQUEST",unit=arty.uid,target=bid,aim=[round(aim[0],1),round(aim[1],1)])
        return True

    def _combat_step(self):
        for u in self.units.values():
            if u.alive:
                u.mark_unavailable_fire_cycles()
        groups=self._build_engagements(); self.engagements=[]; engaged_ids=set()
        for idx,members in enumerate(groups,1):
            blue=[u for u in members if u.side.value=="BLUE" and u.alive]; red=[u for u in members if u.side.value=="RED" and u.alive]
            if not blue or not red:continue
            engaged_ids.update(u.uid for u in members); cx=sum(u.pos[0] for u in members)/len(members); cy=sum(u.pos[1] for u in members)/len(members)
            self.engagements.append({"id":f"E{idx}","center":(cx,cy),"members":[u.uid for u in members],"blue":len(blue),"red":len(red)})
            # Formation target_id remains a primary observation/mission cue, while actual direct
            # fire is allocated independently by FormationElement/weapon stream.  Shared allocation
            # counters add diminishing returns so multiple friendly formations do not all pile every
            # eligible stream onto the same enemy formation when alternatives are available.
            blue_alloc={}; red_alloc={}
            passes=[(blue,red,blue_alloc),(red,blue,red_alloc)]
            # Alternate which side resolves first each step so neither side systematically gets
            # the first RNG draws or the first immediate side effects (cues, structure damage).
            self._combat_step_index=getattr(self,"_combat_step_index",0)+1
            if self._combat_step_index%2==0:
                passes.reverse()
            for shooters,enemies,alloc in passes:
                for s in shooters:
                    if not self._has_direct_weapon(s):continue
                    t=self._select_target(s,enemies)
                    if t:
                        if s.current_order and s.current_order.kind=="ATTACK": s.state=UnitState.ENGAGING
                        self.combat.fire_hybrid(s,enemies,t,alloc)

        # Artillery targeting is perception/doctrine driven. Enemy artillery is treated as a
        # high-payoff counterfire target whenever a credible ARTILLERY track exists, regardless
        # of whether it came from radar, a local observer, or C2 sharing. A radar point-of-origin
        # solution is retained long enough to support follow-on salvos after FDC/reload delays.
        # Any formation with an operational INDIRECT_FIRE weapon participates (mortar sections in
        # infantry units included); the branch label does not decide capability.
        for arty in [u for u in self.units.values() if u.alive and self._has_indirect_weapon(u)]:
            # Explicit infrastructure strike orders supersede autonomous counterfire/fire support.
            if self._execute_infrastructure_strike(arty):
                continue
            counterfire=self._indirect_candidates(arty,"COUNTER_BATTERY")
            if counterfire:
                target=self._select_target(arty,counterfire,"COUNTER_BATTERY")
                if target:
                    for element,weapon in arty.operational_weapons():
                        if weapon.capability.upper()=="INDIRECT_FIRE":
                            self.fire_control.request(arty,target,element,weapon,"COUNTER_BATTERY")
                if bool(self.targeting_doctrine.get("counter_battery",{}).get("prefer_over_other_fire_support",True)):
                    continue
            arty.metadata['_fire_support_engaged_ids']=sorted(engaged_ids)
            cand=self._indirect_candidates(arty,"FIRE_SUPPORT")
            if cand:
                target=self._select_target(arty,cand,"FIRE_SUPPORT")
                if target:
                    for element,weapon in arty.operational_weapons():
                        if weapon.capability.upper()=="INDIRECT_FIRE":
                            self.fire_control.request(arty,target,element,weapon,"FIRE_SUPPORT")


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
            self.belief.on_observation(tr,"ARTILLERY")
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
            n=self.communications.broadcast_side(source,"TRACK_REPORT",q,priority=20)
            self.log("TRACK_DISSEMINATION",source=source,target=q.get("target"),recipients=n,origin="C2")
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
