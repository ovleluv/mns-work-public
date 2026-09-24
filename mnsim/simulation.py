from __future__ import annotations
from .mobility import movement_speed_mps
from typing import Dict, List
from dataclasses import replace
import copy, json, math, random
from .model import Unit, UnitState, Order, FormationElement, UnitType, Track, Side
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

class Simulation:
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
        # Each event is handled at its own timestamp, so follow-on delays (C2 hops, FDC, damage)
        # accumulate from the true event time instead of being rounded up to the tick boundary.
        for ev in self.events.pop_due(end):
            self.time=max(start,float(ev.time))
            self._handle_event(ev.kind, ev.payload)
        self.time=end
        for u in list(self.units.values()):
            if u.alive:
                self._step_unit(u,sim_dt)
        # Scans stay on a fixed k*interval grid, so the scan count over a run does not depend
        # on the integration step or the UI speed multiplier.
        interval=max(1e-3,float(self.combat_config.get("sensor_update_s",1.0)))
        if self.time >= self._next_sensor_update:
            self._sensor_step()
            self._next_sensor_update += interval
            if self._next_sensor_update <= self.time:
                self._next_sensor_update = self.time + interval
        self._combat_step()

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
                u.current_order=branched
                u.metadata.pop("order_started_t",None)
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
                u.metadata.pop("mounted_action_started_t",None); u.current_order=None
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

        # Explicit mission target gets priority when it is perceived.
        contact=self._specific_target_track(u,target_uid,include_lost=True)
        if contact:
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

    def _complete_order(self,u):
        o=u.current_order
        self.log("ORDER_COMPLETE",unit=u.uid,order=o.kind,order_id=o.order_id)
        u.current_order=None
        for key in ("order_started_t","search_arrived_t","objective","mounted_action_started_t","barricade_build_started_t","_building_access_id"):
            u.metadata.pop(key,None)
        self._clear_navigation_state(u)
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
        speed=movement_speed_mps(u,self.terrain,u.pos,move_dest)
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
            u.heading_deg=math.degrees(math.atan2(dy,dx))
        return math.dist(u.pos,tuple(dest))<=arrival

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

    # ---------- formation composition / aggregation ----------
    def aggregate_units(self,new_uid:str,name:str,child_ids:List[str],echelon="COY",pos=None):
        if new_uid in self.units:
            raise ValueError("Aggregate unit ID already exists")
        child_ids = list(dict.fromkeys(child_ids))
        children=[self.units[x] for x in child_ids if x in self.units and self.units[x].active]
        child_ids = [c.uid for c in children]
        if not children: raise ValueError("No active children to aggregate")
        side=children[0].side
        if any(c.side!=side for c in children): raise ValueError("Cannot aggregate opposing sides")
        branch=children[0].branch if len({c.branch for c in children})==1 else "COMBINED"
        elems={}
        from .firepower import _crew_spec
        from .ownership import remap_element
        for c in children:
            ids = {eid: f"{c.uid}:{eid}" for eid in c.elements}
            for eid, e in c.elements.items():
                ce = remap_element(e, ids)
                # Resolve role-based pools before combining units, so operators from
                # another platoon cannot silently staff this platoon's platforms.
                if e.category.upper() == "EQUIPMENT" and "crew_elements" not in e.metadata:
                    crews, _ = _crew_spec(c, e)
                    if crews is not None:
                        ce.metadata["crew_elements"] = [ids.get(x, x) for x in crews]
                ce.metadata["source_unit"] = c.uid
                elems[ce.eid] = ce
        if pos is None:
            pos=(sum(c.pos[0] for c in children)/len(children),sum(c.pos[1] for c in children)/len(children))
        speed=min(c.unit_type.max_speed_mps for c in children); detect=max(c.unit_type.detection_range_m for c in children)
        shared_md=copy.deepcopy(children[0].unit_type.metadata) if len({c.unit_type.name for c in children})==1 else {"mobility_class":"MIXED","terrain_speed_factors":{"OPEN":0.85,"ROAD":1.0,"BRIDGE":0.8}}
        typ=UnitType(name=f"AGG_{branch}",branch=branch,max_speed_mps=speed,detection_range_m=detect,elements=[],metadata=shared_md)
        parent=Unit(uid=new_uid,name=name,side=side,echelon=echelon,unit_type=typ,pos=pos,
                    heading_deg=children[0].heading_deg,watch_heading_deg=children[0].watch_heading_deg,
                    elements=elems,children=list(child_ids),metadata={"aggregated_from":list(child_ids)},
                    # The aggregate knows what its subordinates knew (best track per contact).
                    local_tracks=self._merge_tracks([c.local_tracks for c in children]))
        # Remember each subordinate's place in the formation so deaggregation restores the layout.
        parent.metadata["child_offsets"]={c.uid:(c.pos[0]-pos[0],c.pos[1]-pos[1]) for c in children}
        for c in children: c.active=False; c.state=UnitState.AGGREGATED; c.parent_id=new_uid
        self.add_unit(parent); self.log("AGGREGATE",parent=new_uid,children=child_ids)
        return parent

    def deaggregate_unit(self,parent_uid:str):
        from .ownership import remap_element
        p = self.units[parent_uid]
        if p.metadata.get("deaggregated") or "aggregated_from" not in p.metadata:
            return []
        # A nested aggregate must first be returned by its owning aggregate.
        if p.state == UnitState.AGGREGATED and p.parent_id:
            return []
        child_ids = list(p.metadata["aggregated_from"])
        restored = []
        for cid in child_ids:
            if cid not in self.units:
                continue
            c = self.units[cid]
            originals = c.elements
            prefix = f"{cid}:"
            ids = {eid: eid[len(prefix):] for eid, e in p.elements.items()
                   if e.metadata.get("source_unit") == cid}
            # Include removed elements in the reference mapping as well.
            ids.update({f"{cid}:{eid}": eid for eid in originals})

            def restore_element(element):
                result = remap_element(element, ids)
                old = originals.get(result.eid)
                result.metadata.pop("source_unit", None)
                if old is not None and "source_unit" in old.metadata:
                    result.metadata["source_unit"] = old.metadata["source_unit"]
                return result

            # Restore complete live state, never the stale pre-aggregation inventory.
            c.elements = {ids[eid]: restore_element(e) for eid, e in p.elements.items()
                          if e.metadata.get("source_unit") == cid}
            # Children created while aggregated keep their own inventories. Move only
            # their lineage/references back to the source formation, including nested
            # detached vehicle -> escaped crew families.
            def restore_family(unit):
                unit.elements = {ids.get(eid, eid): restore_element(e)
                                 for eid, e in unit.elements.items()}
                for key in ("destroyed_vehicle_element",):
                    if key in unit.metadata:
                        unit.metadata[key] = ids.get(unit.metadata[key], unit.metadata[key])
                for child_uid in unit.children:
                    child = self.units.get(child_uid)
                    if child is not None and child.parent_id == unit.uid:
                        restore_family(child)

            for uid in list(p.children):
                if uid in child_ids:
                    continue
                child = self.units.get(uid)
                if child is None or {e.metadata.get("source_unit")
                                     for e in child.elements.values()} != {cid}:
                    continue
                restore_family(child)
                child.parent_id = cid
                for key in ("detached_from", "escaped_from", "dismounted_from"):
                    if child.metadata.get(key) == p.uid:
                        child.metadata[key] = cid
                if uid not in c.children:
                    c.children.append(uid)
                p.children.remove(uid)
                record = p.metadata.get("detached_items", {}).pop(uid, None)
                if record is not None:
                    record["element"] = ids.get(record["element"], record["element"])
                    c.metadata.setdefault("detached_items", {})[uid] = record
            c.active = True
            c.state = UnitState.IDLE if c.current_strength > 0 else UnitState.DESTROYED
            c.parent_id = None
            c.pos = self._deaggregated_position(p, c)
            # Orders and perception from before aggregation are stale: children continue the
            # aggregate's mission with the aggregate's (newer) picture of the enemy.
            c.current_order = copy.deepcopy(p.current_order)
            c.order_queue = copy.deepcopy(p.order_queue)
            c.local_tracks = self._merge_tracks([c.local_tracks, p.local_tracks])
            c.target_id = None
            self._clear_navigation_state(c)
            for key in ("order_started_t","search_arrived_t","target_acquired_t","_direct_fire_state",
                        "_local_direct_target_locks","_direct_fire_cycle_state"):
                c.metadata.pop(key, None)
            restored.append(cid)
        p.active = False
        p.state = UnitState.AGGREGATED
        p.metadata["deaggregated"] = True
        self.log("DEAGGREGATE", parent=parent_uid, children=restored)
        return restored

    @staticmethod
    def _merge_tracks(track_maps):
        """Best track per target across several track databases (current > lost, then confidence)."""
        merged={}
        for tracks in track_maps:
            for tid,tr in tracks.items():
                old=merged.get(tid)
                rank=(tr.state=="DESTROYED",tr.state!="LOST",tr.last_seen_time,tr.confidence)
                if old is None or rank>(old.state=="DESTROYED",old.state!="LOST",old.last_seen_time,old.confidence):
                    merged[tid]=copy.deepcopy(tr)
        return merged

    def _deaggregated_position(self, parent: Unit, child: Unit):
        off=dict(parent.metadata.get("child_offsets",{})).get(child.uid)
        if off is None:
            return parent.pos
        cand=(parent.pos[0]+float(off[0]),parent.pos[1]+float(off[1]))
        w=float(self.world.get("width_m",cand[0])); h=float(self.world.get("height_m",cand[1]))
        cand=(max(0.0,min(w,cand[0])),max(0.0,min(h,cand[1])))
        if self.terrain and not self.terrain.passable(child,cand):
            return parent.pos
        return cand

    # ---------- fog of war / local perception ----------
    def _target_signature(self, target: Unit) -> float:
        base = {"INFANTRY":0.72, "ARMOR":1.25, "ARTILLERY":1.00}.get(target.branch,0.85)
        if target.state == UnitState.DEFENDING:
            base *= 0.82
        if target.state in (UnitState.MOVING, UnitState.ATTACKING, UnitState.RETREATING):
            base *= 1.08
        return base

    @staticmethod
    def _angle_delta_deg(a: float, b: float) -> float:
        """Smallest signed angular difference a-b in degrees."""
        return (float(a) - float(b) + 180.0) % 360.0 - 180.0

    def _visual_sensor_profile(self, unit: Unit, target: Unit | None = None):
        """Return effective observation geometry after environment/terrain degradation.

        Baseline geometry belongs to the TO&E/branch sensor profile.  Weather, illumination and
        terrain only multiply that baseline through EnvironmentObservationModel.  This keeps future
        brush/forest/urban/fog/rain/smoke/LOS implementations out of the tactical sensor logic.
        Radar is handled separately and remains omnidirectional unless a radar model says otherwise.
        """
        sensor_mode,forward,fov,close,slew=self._visual_sensor_base(unit)
        mods=self.environment.modifier(self.terrain, unit, target, sensor_mode=sensor_mode)
        forward*=mods.range_factor
        fov*=mods.fov_factor
        close*=mods.awareness_factor
        forward=min(forward, float(self.combat_config.get("visibility_range_m", 1e9)))
        return max(1.0,forward), max(1.0,min(360.0,fov)), max(0.0,close), max(1.0,slew), max(0.0,mods.detection_factor)

    def _visual_sensor_base(self, unit: Unit):
        """Baseline (sensor_mode, forward_m, fov_deg, close_m, slew_deg_s) before any degradation."""
        profiles=dict(self.combat_config.get("visual_sensor_profiles", {}))
        md=dict(profiles.get("DEFAULT", {}))
        md.update(dict(profiles.get(unit.branch, {})))
        md.update(dict(unit.unit_type.metadata.get("visual_sensor", {})))
        sensor_mode=str(md.get("sensor_mode", "VISUAL")).upper()
        forward=float(md.get("forward_range_m", unit.unit_type.detection_range_m))
        fov=float(md.get("forward_fov_deg", self.combat_config.get("visual_forward_fov_deg", 90.0)))
        close=float(md.get("all_round_awareness_m", self.combat_config.get("visual_all_round_awareness_m", 160.0)))
        slew=float(md.get("watch_slew_deg_per_s", self.combat_config.get("visual_watch_slew_deg_per_s", 60.0)))
        return sensor_mode,forward,fov,close,slew

    # Public, UI-facing read-only queries (frontends must not call underscored engine internals).
    def visual_sensor_profile(self, unit: Unit, target: Unit | None = None):
        return self._visual_sensor_profile(unit, target)

    def radar_element_operational(self, unit: Unit, element: FormationElement) -> bool:
        return self._radar_element_operational(unit, element)

    def _sensor_cull_range(self, obs: Unit) -> float:
        """Upper bound on any distance at which ``obs`` could possibly detect a target.

        Terrain/weather factors are multiplicative degradations; an authored factor above 1.0
        disables culling so no detection that the full model would allow is ever skipped.
        """
        _,forward,_,close,_=self._visual_sensor_base(obs)
        boost=float(getattr(self,"_sensor_boost_bound",1.0))
        prox=float(self.combat_config.get("proximity_contact_m",60.0))
        return max(forward,close,prox)*boost+1.0

    def _refresh_sensor_boost_bound(self):
        bound=1.0
        zones=list(self.terrain.data.get("observation_zones",[])) if self.terrain else []
        zones+=list(self.terrain.areas) if self.terrain else []
        for z in zones:
            raw=dict(z.get("observation_modifier",{}))
            for ov in dict(z.get("sensor_overrides",{})).values():
                raw.update(dict(ov))
            for k in ("range_factor","awareness_factor"):
                bound=max(bound,float(raw.get(k,1.0)))
        env=dict(self.combat_config.get("environment",{}))
        for table in ("weather_observation_modifiers","illumination_observation_modifiers"):
            for raw in dict(env.get(table,{})).values():
                raw=dict(raw)
                for ov in dict(raw.get("sensor_overrides",{})).values():
                    raw.update(dict(ov))
                for k in ("range_factor","awareness_factor"):
                    bound=max(bound,float(raw.get(k,1.0)))
        self._sensor_boost_bound=float("inf") if bound>1.0 else 1.0

    def _register_threat_cue(self, unit: Unit, source_uid: str | None, cue_type: str = "DIRECT_FIRE"):
        """Record a short-lived directional cue from incoming fire without creating a Track."""
        source=self.units.get(source_uid) if source_uid else None
        if source is None or not source.alive or source.side==unit.side:
            return
        profiles=dict(self.combat_config.get("threat_cue_profiles", {}))
        cfg=dict(profiles.get("DEFAULT", {})); cfg.update(dict(profiles.get(unit.branch, {})))
        cue=str(cue_type).upper()
        if cue.startswith("INDIRECT") or cue.startswith("ARTILLERY"):
            error=float(cfg.get("indirect_bearing_error_deg",35.0)); memory=float(cfg.get("indirect_memory_s",8.0))
        else:
            error=float(cfg.get("direct_bearing_error_deg",10.0)); memory=float(cfg.get("direct_memory_s",12.0))
        dx=source.pos[0]-unit.pos[0]; dy=source.pos[1]-unit.pos[1]
        if abs(dx)+abs(dy)<=1e-9: return
        bearing=(math.degrees(math.atan2(dy,dx))+self.rng.gauss(0.0,max(0.0,error)))%360.0
        unit.metadata["threat_cue_heading_deg"]=bearing
        unit.metadata["threat_cue_source_uid"]=source.uid
        unit.metadata["threat_cue_until_t"]=self.time+max(0.0,memory)
        unit.metadata["threat_cue_type"]=cue
        self.log("THREAT_CUE",unit=unit.uid,cue_type=cue,bearing_deg=round(bearing,1),memory_s=round(memory,1))

    def _register_shared_situational_cue(self, unit: Unit, estimated_pos, confidence: float, source_uid: str | None = None, cue_kind: str = "CONTACT"):
        """Orient attention toward friendly-reported enemy activity without triggering maneuver.

        Shared cues are intentionally weaker than a direct incoming-fire cue and an explicitly held
        target. Hysteresis prevents several friendly reports from whipping the observation sector
        back and forth every message.
        """
        cfg=dict(self.combat_config.get("shared_situational_cue", {}))
        confidence=float(confidence)
        if confidence < float(cfg.get("min_confidence",0.28)):
            return
        dx=float(estimated_pos[0])-unit.pos[0]; dy=float(estimated_pos[1])-unit.pos[1]
        if abs(dx)+abs(dy)<=1e-9:
            return
        cue_kind=str(cue_kind).upper()
        kind_weight=float(dict(cfg.get("kind_weight",{})).get(cue_kind,1.0))
        new_score=confidence*kind_weight
        old_until=float(unit.metadata.get("shared_cue_until_t",-1e9))
        old_score=float(unit.metadata.get("shared_cue_score",0.0))
        replace_ratio=float(cfg.get("replace_score_ratio",1.10))
        if self.time <= old_until and new_score < old_score*replace_ratio:
            return
        bearing=math.degrees(math.atan2(dy,dx))%360.0
        memory=float(cfg.get("engaged_memory_s",24.0) if cue_kind=="CONTACT_ENGAGED" else cfg.get("memory_s",18.0))
        unit.metadata["shared_cue_heading_deg"]=bearing
        unit.metadata["shared_cue_until_t"]=self.time+memory
        unit.metadata["shared_cue_source_uid"]=source_uid
        unit.metadata["shared_cue_kind"]=cue_kind
        unit.metadata["shared_cue_score"]=new_score
        self.log("SHARED_SA_CUE",unit=unit.uid,source=source_uid,cue_kind=cue_kind,bearing_deg=round(bearing,1),confidence=round(confidence,2))

    def publish_engagement_contact(self, shooter: Unit, track: Track):
        """Publish a throttled contact-engaged report using only the shooter's perceived Track.

        The report communicates situational awareness; it never creates movement/fire-support
        orders in recipients. Future communications routing decides which echelons/nets hear it.
        """
        cfg=dict(self.combat_config.get("communications", {}))
        if not bool(cfg.get("engagement_reports_enabled",True)):
            return
        key=(shooter.uid,track.target_id)
        interval=float(cfg.get("engagement_report_min_interval_s",15.0))
        if self.time-self._last_engagement_report.get(key,-1e9)<interval:
            return
        p=float(cfg.get("engagement_report_probability",0.88))
        if self.rng.random()>p:
            return
        self._last_engagement_report[key]=self.time
        payload={"target":track.target_id,"side":shooter.side.value,"estimated_pos":track.estimated_pos,
                 "position_error_m":track.position_error_m*1.05,"classification":track.classification,
                 "confidence":max(.20,track.confidence*.96),"state":track.state,
                 "track_source":"SHARED","observation_time":track.last_seen_time,
                 "cue_kind":"CONTACT_ENGAGED","perceived_tags":list(track.perceived_tags or ())}
        n=self.communications.broadcast_side(shooter.uid,"TRACK_REPORT",payload,priority=25)
        self.log("CONTACT_ENGAGED_REPORT",source=shooter.uid,target=track.target_id,recipients=n)

    @staticmethod
    def _track_is_close_awareness(track: Track | None) -> bool:
        return bool(track and str(getattr(track, "observation_zone", "UNKNOWN")).upper()=="CLOSE")

    def _desired_watch_heading(self, unit: Unit) -> float:
        # Fresh incoming fire temporarily takes observation priority. This is an orientation cue
        # only: it does not identify the shooter or create a firing-quality FoW Track.
        if self.time <= float(unit.metadata.get("threat_cue_until_t",-1e9)):
            cue_uid=unit.metadata.get("threat_cue_source_uid")
            cue_track=unit.local_tracks.get(cue_uid) if cue_uid else None
            # Incoming fire from inside the short-range all-round awareness zone is handled by
            # local close-combat allocation and must not whip the formation's principal watch
            # sector back and forth. Long-range fire still redirects attention as before.
            preserve_close=bool(self.combat_config.get("close_contacts_preserve_watch_sector",True))
            if not (preserve_close and self._track_is_close_awareness(cue_track)):
                return float(unit.metadata.get("threat_cue_heading_deg",unit.watch_heading_deg))
        # A currently held non-close target/track has priority. A CLOSE contact is already covered
        # by all-round local awareness, so it does not redefine the principal observation sector.
        if unit.target_id:
            tr=unit.local_tracks.get(unit.target_id)
            preserve_close=bool(self.combat_config.get("close_contacts_preserve_watch_sector",True))
            if tr and tr.state != "LOST" and not (preserve_close and self._track_is_close_awareness(tr)):
                dx=tr.estimated_pos[0]-unit.pos[0]; dy=tr.estimated_pos[1]-unit.pos[1]
                if abs(dx)+abs(dy)>1e-9:
                    return math.degrees(math.atan2(dy,dx))
        # A friendly-reported enemy contact can redirect observation, but never creates a movement
        # order or support action by itself. Local doctrine/COA remains responsible for that choice.
        if self.time <= float(unit.metadata.get("shared_cue_until_t",-1e9)):
            return float(unit.metadata.get("shared_cue_heading_deg",unit.watch_heading_deg))
        # During movement the formation's principal observation naturally follows the axis of advance.
        if unit.state in (UnitState.MOVING,UnitState.ATTACKING,UnitState.RETREATING,UnitState.SEARCHING):
            return float(unit.heading_deg)
        # A halt/defend order may explicitly assign a principal observation direction.
        if unit.current_order:
            params=unit.current_order.params
            if "watch_heading_deg" in params:
                return float(params["watch_heading_deg"])
            if "facing_deg" in params:
                return float(params["facing_deg"])
        # Halted/defending formations orient toward their assigned objective/sector if available.
        objective=unit.metadata.get("objective")
        if objective:
            dx=float(objective[0])-unit.pos[0]; dy=float(objective[1])-unit.pos[1]
            if abs(dx)+abs(dy)>1e-9:
                return math.degrees(math.atan2(dy,dx))
        return float(unit.watch_heading_deg)

    def _update_watch_heading(self, unit: Unit, dt: float):
        _,_,_,slew,_=self._visual_sensor_profile(unit)
        desired=self._desired_watch_heading(unit)
        delta=self._angle_delta_deg(desired,unit.watch_heading_deg)
        max_turn=slew*max(float(dt),0.0)
        if abs(delta)<=max_turn:
            unit.watch_heading_deg=desired%360.0
        else:
            unit.watch_heading_deg=(unit.watch_heading_deg + math.copysign(max_turn,delta))%360.0

    def _visual_target_geometry(self, obs: Unit, tgt: Unit):
        """Return (eligible, range_limit, angular_factor, in_all_round_zone)."""
        forward,fov,close,_,env_detection=self._visual_sensor_profile(obs,tgt)
        d=obs.distance_to(tgt)
        if d<=close:
            return True, max(close,1.0), float(self.combat_config.get("visual_all_round_detection_factor",0.72))*env_detection, True
        if d>forward:
            return False, forward, 0.0, False
        bearing=math.degrees(math.atan2(tgt.pos[1]-obs.pos[1],tgt.pos[0]-obs.pos[0]))
        off=abs(self._angle_delta_deg(bearing,obs.watch_heading_deg))
        half=max(0.5,fov*0.5)
        if off>half:
            return False, forward, 0.0, False
        # Central gaze is best; sector edge retains reduced detection probability.
        angular=max(0.25,1.0-0.65*(off/half)**1.5)
        return True, forward, angular*env_detection, False

    def _sensor_step(self):
        stale_s=float(self.combat_config.get("track_stale_s",18.0)); lost_s=float(self.combat_config.get("track_lost_s",45.0))
        # Age all tracks. Counter-battery point-of-origin solutions remain tactically useful
        # longer than visual contacts because the coordinate itself does not vanish when the
        # firing battery stops emitting. Their longer memory is doctrine-configurable.
        cb_cfg=dict(self.targeting_doctrine.get("counter_battery", {}))
        sensor_dt=max(1e-3,float(self.combat_config.get("sensor_update_s",1.0)))
        # Decay factors are defined per second of track age, not per scan, so changing the
        # scan interval does not change how fast an unobserved contact fades.
        lost_decay=float(self.combat_config.get("track_lost_confidence_decay_per_s",0.92))**sensor_dt
        stale_decay=float(self.combat_config.get("track_stale_confidence_decay_per_s",0.97))**sensor_dt
        for obs in self.units.values():
            for tr in obs.local_tracks.values():
                if tr.state=="DESTROYED":
                    continue
                age=self.time-tr.last_seen_time
                tr_stale=stale_s; tr_lost=lost_s
                if tr.source=="COUNTER_BATTERY":
                    tr_stale=float(cb_cfg.get("stale_after_s",60.0))
                    tr_lost=float(cb_cfg.get("lost_after_s",180.0))
                if age > tr_lost:
                    tr.state="LOST"; tr.confidence*=lost_decay
                elif age > tr_stale:
                    tr.state="STALE"; tr.confidence*=stale_decay
                self.belief.age(tr)

        self._refresh_sensor_boost_bound()
        live_targets=[u for u in self.units.values() if u.alive]
        for obs in [u for u in self.units.values() if u.can_observe]:
            self._update_watch_heading(obs,sensor_dt)
            cull=self._sensor_cull_range(obs)
            for tgt in live_targets:
                if tgt.side == obs.side:
                    continue
                d=obs.distance_to(tgt)
                # Cheap range rejection before any terrain ray casting.
                if d > cull:
                    continue
                eligible,r,angular_factor,in_all_round=self._visual_target_geometry(obs,tgt)
                prev=obs.local_tracks.get(tgt.uid)
                proximity=float(self.combat_config.get("proximity_contact_m",60.0))
                # CLOSE/proximity awareness is not x-ray vision.  A terrain/environment LOS layer
                # may hard-block the path; do not let the probability floor resurrect that target.
                if angular_factor <= 0.0:
                    continue
                if d <= proximity:
                    n=(prev.observations+1) if prev else 1
                    conf=max(0.88,prev.confidence if prev else 0.0)
                    err=max(3.0,min(float(self.combat_config.get("proximity_contact_error_m",12.0)),3.0+0.15*d))
                    ang=self.rng.random()*math.tau; mag=abs(self.rng.gauss(0,err*0.35))
                    est=(tgt.pos[0]+math.cos(ang)*mag,tgt.pos[1]+math.sin(ang)*mag)
                    obs.local_tracks[tgt.uid]=Track(track_id=f"{obs.uid}:{tgt.uid}",target_id=tgt.uid,
                        estimated_pos=est,position_error_m=err,classification=tgt.branch,confidence=conf,
                        last_seen_time=self.time,observations=n,source="PROXIMITY",observation_zone="CLOSE",state="IDENTIFIED",
                        belief_confidence=max(conf,0.75),existence_confirmed=True,last_confirmed_time=self.time,
                        perceived_tags=self.combat.observed_target_tags(tgt))
                    self.belief.on_observation(obs.local_tracks[tgt.uid],tgt.branch)
                    if prev is None or prev.state in ("STALE","LOST"):
                        self.log("PROXIMITY_CONTACT",observer=obs.uid,target=tgt.uid,distance=round(d,1),confidence=round(conf,2))
                    continue
                if not eligible: continue
                # Directional visual envelope is a hard maximum, not guaranteed instantaneous detection.
                # Detection probability falls sharply toward the edge and is re-evaluated each sensor scan.
                exp=float(self.combat_config.get("visual_detection_range_exponent",2.0))
                range_factor=max(0.0,1.0-(d/r)**exp)
                edge_p=float(self.combat_config.get("visual_detection_edge_p_per_scan",0.01))
                max_p=float(self.combat_config.get("visual_detection_max_p_per_scan",0.55))
                p=max(0.001,min(0.98,(edge_p+max_p*range_factor*self._target_signature(tgt))*angular_factor))
                if self.rng.random() > p: continue
                n=(prev.observations+1) if prev else 1
                conf=min(0.98,(prev.confidence if prev else 0.18)+0.16+0.16*range_factor)
                err=max(8.0,(1.0-conf)*180.0)
                ang=self.rng.random()*math.tau; mag=abs(self.rng.gauss(0,err*0.45))
                est=(tgt.pos[0]+math.cos(ang)*mag,tgt.pos[1]+math.sin(ang)*mag)
                state="DETECTED"; cls="UNKNOWN"
                if n>=2 or conf>=0.52: state="CLASSIFIED"; cls=tgt.branch
                if n>=4 or conf>=0.82: state="IDENTIFIED"; cls=tgt.branch
                tr=Track(track_id=f"{obs.uid}:{tgt.uid}",target_id=tgt.uid,estimated_pos=est,
                         position_error_m=err,classification=cls,confidence=conf,last_seen_time=self.time,
                         observations=n,source="LOCAL",observation_zone=("CLOSE" if in_all_round else "FORWARD"),state=state,
                         belief_confidence=max(prev.belief_confidence if prev else 0.0,conf),
                         existence_confirmed=True,last_confirmed_time=self.time,
                         perceived_tags=self.combat.observed_target_tags(tgt))
                self.belief.on_observation(tr,cls)
                obs.local_tracks[tgt.uid]=tr
                if prev is None or prev.state in ("STALE","LOST"):
                    self.log("TRACK_UPDATE",observer=obs.uid,target=tgt.uid,state=state,confidence=round(conf,2),source="LOCAL")
                # Local observation is not instantly available to every friendly unit.
                # First, the observer must report it; only then can C2 disseminate the report.
                report_p=float(self.combat_config.get("report_probability_per_observation",0.42))
                report_key=(obs.uid,tgt.uid)
                min_report_interval=float(self.combat_config.get("observer_report_min_interval_s",12.0))
                can_report=(self.time-self._last_report_sent.get(report_key,-1e9))>=min_report_interval
                if can_report and self.rng.random() < report_p:
                    self._last_report_sent[report_key]=self.time
                    delay=self.rng.uniform(
                        float(self.combat_config.get("observer_report_delay_min_s",8.0)),
                        float(self.combat_config.get("observer_report_delay_max_s",20.0))
                    )
                    self.events.push(
                        self.time+delay,"TRACK_REPORT_TO_HQ",
                        source=obs.uid,target=tgt.uid,side=obs.side.value,
                        estimated_pos=est,position_error_m=err,classification=cls,
                        confidence=max(.20,conf*.90),state=state,observation_time=self.time,
                        track_source="SHARED",perceived_tags=list(tr.perceived_tags or ()),
                    )
                    self.log("TRACK_REPORT_SENT",source=obs.uid,target=tgt.uid,delay_s=round(delay,1))

    def _radar_element_operational(self, unit: Unit, radar_el: FormationElement) -> bool:
        if not radar_el.operational or radar_el.role.upper() != "COUNTER_BATTERY_RADAR":
            return False
        required_role=str(radar_el.metadata.get("operator_role","RADAR_CREW")).upper()
        min_crew=int(radar_el.metadata.get("min_crew",1))
        crew=sum(e.count for e in unit.elements.values()
                 if e.category.upper()=="PERSONNEL" and e.role.upper()==required_role and e.alive)
        return crew >= min_crew

    def _operational_counter_battery_radars(self, side: Side):
        """Return operational counter-battery radar elements for a side.

        Radar performance values come from element metadata. Demo values are synthetic,
        generic M&S tuning parameters rather than specifications for a real system.
        """
        out=[]
        for u in self.units.values():
            if not u.alive or u.side != side:
                continue
            for e in u.elements.values():
                if self._radar_element_operational(u,e):
                    out.append((u,e))
        return out


    def notify_indirect_fire_launch(self, shooter: Unit, weapon_name: str, mission_mode: str = "INDIRECT_FIRE",
                                    projectile_count: int = 1):
        """Broadcast hostile indirect-fire trajectories to eligible counter-battery sensors.

        Detection depends on the firing unit's location relative to an operational enemy radar,
        not on what the shooter is targeting. A multi-round salvo provides more than one
        trajectory to acquire, but the opportunities are capped because rounds from the same
        salvo are strongly correlated rather than independent sensor trials.
        """
        projectile_count=max(1,int(projectile_count))
        self.log("INDIRECT_LAUNCH", shooter=shooter.uid, weapon=weapon_name, mode=mission_mode,
                 projectile_count=projectile_count)
        self._counter_battery_observe(shooter, weapon_name, projectile_count)

    @staticmethod
    def _counter_battery_salvo_probability(single_projectile_p: float, projectile_count: int, metadata: dict):
        """Convert per-trajectory acquisition probability into a capped salvo probability.

        Public weapon-locating-radar descriptions support multi-projectile tracking, but do not
        publish a generally applicable distance-dependent P(detect).  The cap therefore remains
        a data-driven M&S tuning parameter rather than a claim about a particular radar.
        """
        p=max(0.0,min(0.99,float(single_projectile_p)))
        cap=max(1,int(metadata.get("max_salvo_detection_opportunities",3)))
        opportunities=max(1,min(max(1,int(projectile_count)),cap))
        p_salvo=1.0-(1.0-p)**opportunities
        return max(0.0,min(0.99,p_salvo)),opportunities

    def _counter_battery_observe(self, shooter: Unit, weapon_name: str, projectile_count: int = 1):
        """Attempt a fire-origin solution after hostile indirect fire.

        A surviving radar must be within instrumented range. Probability decreases and
        error increases with range. A short processing delay is modeled before the track
        enters the radar unit's local fog-of-war database.
        """
        enemy_side = Side.RED if shooter.side == Side.BLUE else Side.BLUE
        for radar_unit, radar_el in self._operational_counter_battery_radars(enemy_side):
            md=radar_el.metadata
            max_r=float(md.get("radar_range_m",3200.0))
            d=radar_unit.distance_to(shooter)
            if d > max_r:
                continue
            x=max(0.0,min(1.0,d/max(max_r,1.0)))
            p_near=float(md.get("detect_p_near",0.82))
            p_edge=float(md.get("detect_p_edge",0.28))
            p_single=max(0.0,min(0.99,p_edge+(p_near-p_edge)*(1.0-x**1.7)))
            p,opportunities=self._counter_battery_salvo_probability(p_single,projectile_count,md)
            if self.rng.random() > p:
                self.log("CB_RADAR_MISS",radar=radar_unit.uid,source=shooter.uid,
                         distance=round(d,1),weapon=weapon_name,projectile_count=max(1,int(projectile_count)),
                         trajectory_opportunities=opportunities,single_projectile_p=round(p_single,3),
                         salvo_detect_p=round(p,3))
                continue

            err_min=float(md.get("position_error_min_m",35.0))
            err_max=float(md.get("position_error_max_m",180.0))
            nominal_err=err_min+(err_max-err_min)*(x**1.35)
            prev=radar_unit.local_tracks.get(shooter.uid)
            n=(prev.observations+1) if prev and prev.source=="COUNTER_BATTERY" else 1
            salvo_solution_gain=max(0.0,float(md.get("salvo_solution_gain_exponent",0.20)))
            err=max(err_min*0.65, nominal_err/((n**0.42)*(opportunities**salvo_solution_gain)))
            ang=self.rng.random()*math.tau
            mag=abs(self.rng.gauss(0.0,err*0.48))
            est=(shooter.pos[0]+math.cos(ang)*mag, shooter.pos[1]+math.sin(ang)*mag)
            conf0=0.44+0.34*(1.0-x)+0.04*(opportunities-1)
            conf=min(0.96,max(conf0,(prev.confidence if prev else 0.0)+0.10))
            state="CLASSIFIED" if conf < 0.78 else "IDENTIFIED"
            delay=self.rng.uniform(float(md.get("processing_delay_min_s",2.0)),
                                   float(md.get("processing_delay_max_s",6.0)))
            self.events.push(self.time+delay,"CB_TRACK_READY",radar=radar_unit.uid,
                             radar_element=radar_el.eid,target=shooter.uid,
                             estimated_pos=est,position_error_m=err,confidence=conf,
                             state=state,distance=d,weapon=weapon_name,observations=n,
                             projectile_count=max(1,int(projectile_count)),trajectory_opportunities=opportunities,
                             single_projectile_p=p_single,salvo_detect_p=p,observation_time=self.time)
            self.log("CB_RADAR_DETECTION_PENDING",radar=radar_unit.uid,source=shooter.uid,
                     distance=round(d,1),processing_delay_s=round(delay,1),weapon=weapon_name,
                     projectile_count=max(1,int(projectile_count)),trajectory_opportunities=opportunities,
                     single_projectile_p=round(p_single,3),salvo_detect_p=round(p,3))

    def side_tracks(self, side: Side):
        merged={}
        for u in self.units.values():
            if u.side != side or not u.active: continue
            for tid,tr in u.local_tracks.items():
                if tr.state=="DESTROYED":
                    continue
                if tr.state=="LOST" and not self.belief.inferred_visible(tr):
                    continue
                old=merged.get(tid)
                # Prefer a current tactical track, then stronger persistent existence belief.
                score=(1 if tr.state!="LOST" else 0,tr.confidence,tr.belief_confidence,-(self.time-tr.last_seen_time))
                if old is None:
                    merged[tid]=tr
                else:
                    oldscore=(1 if old.state!="LOST" else 0,old.confidence,old.belief_confidence,-(self.time-old.last_seen_time))
                    if score>oldscore: merged[tid]=tr
        return merged

    def _track_for(self, observer: Unit, target: Unit, mode: str = "DIRECT"):
        tr=observer.local_tracks.get(target.uid)
        if not tr or tr.state=="DESTROYED":
            return None
        mode=str(mode).upper()
        # Direct fire requires a locally acquired firing-quality Track. Friendly SHARED reports
        # are situational cues only: they may redirect observation, but cannot by themselves make
        # rifle/cannon fire possible. Indirect-fire modes retain their own shared-track rules.
        if mode=="DIRECT" and bool(self.combat_config.get("direct_fire_requires_local_track", True)):
            if str(getattr(tr,"source","LOCAL")).upper() not in ("LOCAL","PROXIMITY"):
                return None
        if mode=="COUNTER_BATTERY" and tr.classification.upper()=="ARTILLERY":
            cfg=dict(self.targeting_doctrine.get("counter_battery", {}))
            fresh_age=float(cfg.get("lost_after_s",180.0))
            belief_fire_age=float(cfg.get("belief_fire_max_age_s",900.0))
            min_conf=float(cfg.get("min_confidence",0.18))
            min_belief=float(cfg.get("min_belief_confidence",0.28))
            fresh_max_err=float(cfg.get("max_position_error_m",300.0))
            belief_max_err=float(cfg.get("belief_fire_max_position_error_m",650.0))
            growth=float(cfg.get("belief_position_error_growth_m_per_min",28.0))
            confidence_half=float(cfg.get("belief_fire_confidence_half_life_s",420.0))
            age=max(0.0,self.time-tr.last_seen_time)
            # Fresh radar/observer solutions remain firing-quality in the normal sense.
            if (tr.state!="LOST" and age<=fresh_age and tr.position_error_m<=fresh_max_err
                    and (tr.confidence>=min_conf or tr.belief_confidence>=min_belief)):
                return tr
            # Losing the radar must not erase a previously established enemy battery.  Doctrine may
            # continue low-precision counterfire on the last known point while existence belief is
            # credible.  Accuracy degrades with age, so this is harassment/re-attack rather than a
            # magically precise stale solution.  A copy is returned so intelligence memory itself
            # is not mutated by repeated fire-control queries.
            if (tr.existence_confirmed and tr.belief_confidence>=min_belief and age<=belief_fire_age):
                eff_err=min(belief_max_err, float(tr.position_error_m)+growth*(age/60.0))
                if eff_err<=belief_max_err:
                    decay=0.5**(age/max(1.0,confidence_half))
                    eff_conf=max(min_conf*0.55, min(float(tr.confidence),float(tr.belief_confidence))*decay)
                    return replace(tr, position_error_m=eff_err, confidence=eff_conf, state="INFERRED")
            return None
        if tr.source == "COUNTER_BATTERY":
            max_age=float(self.combat_config.get("counter_battery_track_max_age_s",60.0))
            min_conf=float(self.combat_config.get("counter_battery_track_min_confidence",0.20))
        else:
            max_age=float(self.combat_config.get("track_lost_s",45.0))
            min_conf=float(self.combat_config.get("track_action_confidence",.35))
        if tr.actionable(self.time,max_age,min_conf):
            return tr
        return None

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
            for s in blue:
                if not self._has_direct_weapon(s):continue
                t=self._select_target(s,red)
                if t:
                    if s.current_order and s.current_order.kind=="ATTACK": s.state=UnitState.ENGAGING
                    self.combat.fire_hybrid(s,red,t,blue_alloc)
            for s in red:
                if not self._has_direct_weapon(s):continue
                t=self._select_target(s,blue)
                if t:
                    if s.current_order and s.current_order.kind=="ATTACK": s.state=UnitState.ENGAGING
                    self.combat.fire_hybrid(s,blue,t,red_alloc)

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



    def _maybe_split_damaged_equipment(self, unit:Unit, element:FormationElement, item_index:int, state:str):
        """Transfer an immobilized armored vehicle out of its parent formation.

        A detached vehicle is a real combat entity, not a display proxy.  Ownership of the
        physical item moves from the parent element to a vehicle-level child:

        * MOBILITY_KILL: stationary, but still fire-capable if its weapon system has ammunition;
        * DISABLED: stationary and not fire-capable;
        * the parent formation keeps only the remaining attached vehicles and therefore moves
          at their normal formation mobility rather than being dragged down by the abandoned item.

        The split is deliberately limited to armor elements tagged for automatic mobility-kill
        separation.  Recovery/rejoin is a future doctrine/logistics operation.
        """
        if unit.metadata.get("detached_from"):
            return
        if "ARMOR" not in element.tags:
            return
        if not bool(element.metadata.get("auto_split_on_mobility_kill", True)):
            return
        if state not in ("MOBILITY_KILL","DISABLED"):
            return
        element.ensure_item_states()
        if item_index < 0 or item_index >= len(element.item_states):
            return

        # Allocate a stable child id independent of the source list index; list indices shift
        # after each physical item is transferred out of the parent formation.
        serial=int(unit.metadata.get("_detach_serial",0))+1
        unit.metadata["_detach_serial"]=serial
        new_uid=f"{unit.uid}-DET-{serial}"
        while new_uid in self.units:
            serial+=1
            unit.metadata["_detach_serial"]=serial
            new_uid=f"{unit.uid}-DET-{serial}"

        prior_count=max(1, element.count)
        moved_item_id=element.item_id_at(item_index)
        ce=copy.deepcopy(element)
        ce.count=1
        ce.initial_count=1
        ce.item_states=[state]
        ce.item_ids=[moved_item_id] if moved_item_id else []

        # Crew ownership follows the detached platform. Never let an external-crew
        # vehicle silently become an implicitly staffed vehicle in its new unit.
        from .firepower import _crew_spec
        crew_ids, required = _crew_spec(unit, element)
        child_elements = {ce.eid: ce}
        if crew_ids is not None:
            crew_ids = list(dict.fromkeys(crew_ids))
            available = sum(max(0, unit.elements[eid].count) for eid in crew_ids
                            if eid in unit.elements and unit.elements[eid].category.upper() == 'PERSONNEL'
                            and not unit.elements[eid].metadata.get('dismountable', False))
            needed = min(required, available // prior_count)
            ce.metadata['crew_elements'] = []
            for eid in crew_ids:
                crew = unit.elements.get(eid)
                if crew is None or crew.category.upper() != 'PERSONNEL' or crew.metadata.get('dismountable', False):
                    continue
                take = min(max(0, crew.count), needed)
                if not take:
                    continue
                from .ownership import transfer_personnel
                detached_crew = transfer_personnel(crew, take)
                child_elements[eid] = detached_crew
                ce.metadata['crew_elements'].append(eid)
                needed -= take
        elif 'embedded_crew_remaining' in element.metadata:
            available = max(0, int(element.metadata['embedded_crew_remaining']))
            share = min(required, available // prior_count)
            ce.metadata['embedded_crew_remaining'] = share
            element.metadata['embedded_crew_remaining'] = available - share

        # If ammunition is explicitly finite, transfer a proportional vehicle share instead of
        # cloning the formation ammunition pool. Unlimited/abstract ammo (-1) remains unlimited.
        for parent_w, child_w in zip(element.weapons, ce.weapons):
            if parent_w.ammo_remaining >= 0:
                share=0 if parent_w.ammo_remaining==0 else max(1, parent_w.ammo_remaining//prior_count)
                share=min(share,parent_w.ammo_remaining)
                child_w.ammo_remaining=share
                parent_w.ammo_remaining-=share
            if parent_w.ammo_capacity >= 0:
                cap_share=0 if parent_w.ammo_capacity==0 else max(1,parent_w.ammo_capacity//prior_count)
                cap_share=min(cap_share,parent_w.ammo_capacity)
                child_w.ammo_capacity=cap_share
                parent_w.ammo_capacity-=cap_share

        # Transfer physical ownership: remove the vehicle from the parent's equipment list.
        # Decrement initial_count as well so strength accounting remains conserved across the
        # residual formation + detached child instead of double-counting the original vehicle.
        element.remove_item(item_index)
        element.initial_count=max(0,element.initial_count-1)
        element.sync_count_from_states()

        typ=UnitType(name=f"DETACHED_{unit.unit_type.name}",branch=unit.branch,
                     max_speed_mps=0.0,
                     detection_range_m=unit.unit_type.detection_range_m,elements=[],
                     metadata=copy.deepcopy(unit.unit_type.metadata))
        child=Unit(uid=new_uid,name=f"{unit.name} detached {serial}",side=unit.side,
                   echelon="VEH",unit_type=typ,pos=unit.pos,heading_deg=unit.heading_deg,
                   watch_heading_deg=unit.watch_heading_deg,
                   state=UnitState.IDLE,parent_id=unit.uid,elements=child_elements,
                   target_id=unit.target_id,
                   metadata={"detached_from":unit.uid,"damage_state":state,
                             "detachment_reason":state,"actual_detached_vehicle":True},
                   weapon_last_fire=copy.deepcopy(unit.weapon_last_fire),
                   local_tracks=copy.deepcopy(unit.local_tracks))
        # Carry current direct-fire acquisition state so detachment does not create an artificial
        # instant re-shot; the immobilized crew retains the tactical picture it had at separation.
        if "_direct_fire_state" in unit.metadata:
            child.metadata["_direct_fire_state"]=copy.deepcopy(unit.metadata["_direct_fire_state"])
        if "target_acquired_t" in unit.metadata:
            child.metadata["target_acquired_t"]=unit.metadata["target_acquired_t"]

        self.add_unit(child)
        unit.children.append(new_uid)
        unit.metadata.setdefault("detached_items",{})[new_uid]={
            "element":element.eid,"state":state,"detached_at":self.time,
            "position":tuple(unit.pos)
        }
        self.log("DEAGGREGATE_DAMAGED_ITEM",parent=unit.uid,child=new_uid,element=element.eid,
                 source_item_index=item_index,state=state,parent_remaining=element.count)

        # If every physical item has been transferred out, there is no residual aggregate entity.
        if unit.current_strength<=0:
            unit.active=False
            unit.metadata["depleted_by_detachment"]=True
            self.log("FORMATION_DEPLETED_BY_DETACHMENT",unit=unit.uid,children=list(unit.children))
        return child

    def _receive_comm_message(self, recv: Unit, msg: dict):
        if not recv.can_communicate:
            return
        mtype=str(msg.get("message_type","")).upper(); q=dict(msg.get("payload",{}))
        if mtype=="TRACK_REPORT":
            target=q.get("target")
            if not target: return
            old=recv.local_tracks.get(target)
            if str(q.get("state","")).upper()=="DESTROYED":
                self._apply_bda(recv,target,q,source=msg.get("sender_uid"))
                return
            if old is not None and old.state=="DESTROYED":
                return
            incoming_conf=float(q.get("confidence",.3))*0.92
            # A better fresh local observation is never overwritten by weaker shared SA.
            if not (old and old.source=="LOCAL" and old.confidence>=incoming_conf):
                tr=Track(track_id=f"{recv.uid}:{target}",target_id=target,
                    estimated_pos=tuple(q["estimated_pos"]),position_error_m=float(q.get("position_error_m",100))*1.12,
                    classification=q.get("classification","UNKNOWN"),confidence=incoming_conf,
                    last_seen_time=float(q.get("observation_time",self.time)),
                    observations=max(1,old.observations if old else 1),source=q.get("track_source","SHARED"),
                    observation_zone="SHARED",state=q.get("state","DETECTED"),belief_confidence=max(old.belief_confidence if old else 0.0,incoming_conf,0.45),
                    existence_confirmed=True,last_confirmed_time=float(q.get("observation_time",self.time)),
                    perceived_tags=tuple(q.get("perceived_tags") or ()) or None)
                recv.local_tracks[target]=tr
                self.belief.on_observation(tr,q.get("classification","UNKNOWN"))
            # Shared situational awareness may reorient sensors even if local Track was already better.
            self._register_shared_situational_cue(recv,tuple(q["estimated_pos"]),incoming_conf,msg.get("sender_uid"),q.get("cue_kind","CONTACT"))
            self.log("TRACK_SHARED",source=msg.get("sender_uid"),recipient=recv.uid,target=target,
                     channel=msg.get("channel"),confidence=round(incoming_conf,2))
            return
        self.log("COMM_RX_UNHANDLED",recipient=recv.uid,message_type=mtype,source=msg.get("sender_uid"))

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
            self.damage.apply_equipment_effect(t,el,p.get("effect","DISABLED"),
                                               source=p.get("source",""),weapon=p.get("weapon",""),
                                               reason=reason,item_index=item_index)
            if not t.alive:
                self._on_unit_destroyed(t,p.get("source"))
                return
            self._check_reactive_branches(t)
            return
        if kind=="ELEMENT_LOSS":
            t=self.units.get(p["target"])
            if not t or not t.alive:return
            el=t.elements.get(p["element"])
            if not el or el.count<=0:return
            requested=int(p.get("count",1)); actual=min(el.count,requested); el.count-=actual
            self._register_threat_cue(t,p.get("source"),str(p.get("cue_type","DIRECT_FIRE")))
            self.log("ELEMENT_LOSS",target=t.uid,source=p.get("source"),element=el.eid,role=el.role,count=actual,remaining=el.count,personnel=t.personnel,equipment=t.equipment,strength=round(t.strength_ratio,3),weapon=p.get("weapon"))
            if el.count==0:self.log("ELEMENT_DISABLED",unit=t.uid,element=el.eid,role=el.role)
            if t.current_strength<=0:
                t.state=UnitState.DESTROYED; self.log("DESTROYED",unit=t.uid,source=p.get("source"))
                self._on_unit_destroyed(t,p.get("source"))
            else:self._check_reactive_branches(t)

    def _locate_equipment_item(self, unit: Unit, element_eid: str, item_id: str, _depth: int = 0):
        """Find a physical item by id in ``unit`` or in the vehicles detached from it."""
        el=unit.elements.get(element_eid)
        if el is not None:
            idx=el.index_of_item(item_id)
            if idx is not None:
                return unit,el,idx
        if _depth>8:
            return None
        for child_uid in unit.children:
            child=self.units.get(child_uid)
            if child is None or child.parent_id!=unit.uid:
                continue
            found=self._locate_equipment_item(child,element_eid,item_id,_depth+1)
            if found is not None:
                return found
        return None

    # ---------- battle-damage assessment (what friendly forces know about a kill) ----------
    def _on_unit_destroyed(self, target: Unit, source_uid: str | None = None):
        """Give BDA only to enemy formations that were actually watching the target.

        The shooter and any observer holding a fresh LOCAL/PROXIMITY Track see the kill. One of
        them reports it over the communications layer; everybody else keeps a Track that simply
        ages out.  An unobserved kill (e.g. artillery on an untracked coordinate) stays unknown.
        """
        if target.metadata.get("_bda_resolved"):
            return
        target.metadata["_bda_resolved"]=True
        window=float(self.combat_config.get("bda_observation_window_s",6.0))
        witnesses=[]
        for obs in self.units.values():
            if obs.side==target.side or not obs.can_observe:
                continue
            tr=obs.local_tracks.get(target.uid)
            if tr is None or tr.state in ("LOST","DESTROYED"):
                continue
            fresh=str(tr.source).upper() in ("LOCAL","PROXIMITY") and self.time-tr.last_seen_time<=window
            if fresh or obs.uid==source_uid:
                witnesses.append(obs)
        for obs in witnesses:
            self._apply_bda(obs,target.uid,{"estimated_pos":obs.local_tracks[target.uid].estimated_pos},
                            source=obs.uid,local=True)
        if witnesses:
            reporter=next((w for w in witnesses if w.uid==source_uid),witnesses[0])
            tr=reporter.local_tracks[target.uid]
            payload={"target":target.uid,"side":reporter.side.value,"estimated_pos":tr.estimated_pos,
                     "position_error_m":tr.position_error_m,"classification":tr.classification,
                     "confidence":1.0,"state":"DESTROYED","track_source":"BDA",
                     "observation_time":self.time}
            n=self.communications.broadcast_side(reporter.uid,"TRACK_REPORT",payload,priority=28)
            self.log("BDA_REPORT",source=reporter.uid,target=target.uid,witnesses=len(witnesses),recipients=n)
        else:
            self.log("KILL_UNOBSERVED",target=target.uid,source=source_uid)

    def _apply_bda(self, recv: Unit, target_uid: str, q: dict, source=None, local=False):
        old=recv.local_tracks.get(target_uid)
        if old is not None and old.state=="DESTROYED":
            return
        base=old if old is not None else Track(track_id=f"{recv.uid}:{target_uid}",target_id=target_uid,
                                                  estimated_pos=tuple(q.get("estimated_pos",(0.0,0.0))),
                                                  position_error_m=float(q.get("position_error_m",50.0)),
                                                  classification=q.get("classification","UNKNOWN"))
        recv.local_tracks[target_uid]=replace(base,state="DESTROYED",confidence=1.0,
                                              source=base.source if local else "BDA",
                                              last_confirmed_time=self.time)
        if recv.target_id==target_uid:
            recv.target_id=None
            recv.metadata.pop("target_acquired_t",None)
        self.log("BDA_CONFIRMED",unit=recv.uid,target=target_uid,source=source,local=bool(local))

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
        if not ConditionEvaluator.eval_all(self,u,o.conditions):
            return False
        branched=compile_order_fragment(self,u,o.on_true)
        self.log("CONDITION_BRANCH",unit=u.uid,from_order=o.order_id,to_order=branched.kind,phase=o.phase_id)
        u.current_order=branched
        u.metadata.pop("order_started_t",None)
        return True

    def save_log(self,path):
        with open(path,"w",encoding="utf-8") as f:
            for rec in self.logs:f.write(json.dumps(rec,ensure_ascii=False)+"\n")
