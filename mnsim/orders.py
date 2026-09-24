"""Order execution and movement subsystem of :class:`mnsim.simulation.Simulation` (mixin).

Per-unit order stepping (move, attack, defend, structure attack, barricades, building access,
mounted transport), navigation/unreachable handling, terrain transitions and bridge/building
destruction.  Split out of simulation.py without changing behaviour; methods operate on the
Simulation instance (``self``).
"""
from __future__ import annotations
from .mobility import movement_speed_mps
import math
from .model import Unit, UnitState, Order
from .bml import apply_branch, compile_order_fragment
from .mounted import (initialize_transport_metadata, dismount_organic, mount_organic, board_external,
                      disembark_external, free_seats, carrier_operable)


class OrderExecutionMixin:
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
            if not o.deadline_reported:
                o.deadline_reported=True
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
            if not carrier_operable(u):
                u.state=UnitState.DEFENDING; u.metadata["tactical_reason"]="MOUNT / CARRIER INOPERABLE (CREW SHORTAGE)"; return
            if free_seats(self,u)<child.personnel:
                u.state=UnitState.DEFENDING; u.metadata["tactical_reason"]="MOUNT / INSUFFICIENT EMPTY SEATS"; return
            radius=float(u.metadata.get("embark_radius_m",20.0))
            if math.dist(u.pos,child.pos)>radius:
                u.state=UnitState.MOVING; u.metadata["tactical_reason"]="RENDEZVOUS WITH ORGANIC DISMOUNTS"
                self._move_toward(u,tuple(child.pos),dt); return
            u.state=UnitState.MOUNTING; started=u.metadata.setdefault("mounted_action_started_t",self.time)
            if self.time-started < float(u.metadata.get("embark_time_s",20.0)):
                u.metadata["tactical_reason"]="MOUNTING ORGANIC INFANTRY"; return
            if mount_organic(self,u):
                u.metadata.pop("mounted_action_started_t",None); self._complete_order(u)
            return
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
        # Target existence is a belief. Physical destruction alone cannot tell the commanded
        # formation that its mission is complete; only battle-damage information (own
        # observation or a report) or a decayed existence belief can.
        known=u.local_tracks.get(target_uid)
        if known is not None and known.state=="DESTROYED":
            self.log("BML_TARGET_DESTROYED",unit=u.uid,target=target_uid,order=o.kind,
                     bda_source=getattr(known,"source",None))
            self._complete_order(u); return
        if known is not None and not known.existence_confirmed and known.state=="LOST":
            self.log("BML_TARGET_DESTROYED_CONFIRMED",unit=u.uid,target=target_uid,order=o.kind)
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
        # Completion-time conditions still need this order's elapsed time and objective.
        apply_branch(self,u,o)
        self.reset_order_execution_state(u)

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
        if not (0.0 <= float(dest[0]) <= float(self.world["width_m"])
                and 0.0 <= float(dest[1]) <= float(self.world["height_m"])):
            u.metadata["_nav_no_path"]=True
            u.metadata["_nav_no_path_destination"]=tuple(dest)
            return False
        # Terrain may redirect a non-amphibious formation to a bridge before the final destination.
        move_dest=self.terrain.movement_target(u,tuple(dest)) if self.terrain else tuple(dest)
        dx,dy=move_dest[0]-u.pos[0],move_dest[1]-u.pos[1]; d=math.hypot(dx,dy)
        arrival=float(self.combat_config.get("order_arrival_m",3.0))
        if d<1e-9:
            # Reaching a bridge waypoint is not the same as reaching the actual order destination.
            return math.dist(u.pos,tuple(dest))<=arrival
        # Tactical movement speed is data-driven by mobility class/terrain and current tactical state.
        # Unit max_speed_mps is a formation planning speed, not a vehicle brochure top speed.
        # The final assault is a rush at full movement speed, not the cautious ATTACKING pace.
        rush=self.assault.enabled and self.assault.assaulting(u) and u.state in (UnitState.ATTACKING,UnitState.ENGAGING)
        speed=(movement_speed_mps(u,self.terrain,u.pos,move_dest,state=UnitState.MOVING if rush else None)
               *self.stress.movement_factor(u))
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
