
from __future__ import annotations
import math
from .model import Unit, UnitState


# Fallback composition implied by a perceived classification when a Track carries no observed
# element tags (e.g. hand-built or legacy shared tracks).  ``None``/missing means "unknown":
# the shooter cannot rule a weapon out, so the decision layer stays permissive and the physical
# effect is resolved against the real target only when the round arrives.
DEFAULT_CLASSIFICATION_TARGET_TAGS = {
    "ARMOR": ("EQUIPMENT", "ARMOR", "VEHICLE"),
    "ARTILLERY": ("EQUIPMENT", "ARTILLERY", "PERSONNEL"),
    "INFANTRY": ("PERSONNEL",),
    "RECON": ("PERSONNEL",),
    "SPECIAL_OPERATIONS": ("PERSONNEL",),
}


class CombatResolver:
    """Weapon/target compatibility, target selection, and fire resolution.

    This module owns combat resolution mechanics. Formation composition lives in model.py,
    tactical behavior in doctrine.py, perception in Simulation's track subsystem, and UI in main.py.

    Demo weapon probabilities are abstract tuning parameters. They are intentionally not calibrated
    to a particular real weapon system.
    """

    def __init__(self, sim):
        self.sim = sim

    @staticmethod
    def weapon_can_affect(weapon, target: Unit) -> bool:
        """Physical (ground-truth) compatibility.  Use only when resolving an actual effect."""
        tags = {x.upper() for x in weapon.target_tags}
        return any(e.alive and target.element_exposed(e) and bool(e.tags & tags) for e in target.elements.values())

    @staticmethod
    def observed_target_tags(target: Unit) -> tuple:
        """Element tags an observer can see at the moment of an observation (snapshot)."""
        tags=set()
        for e in target.elements.values():
            if e.alive and target.element_exposed(e):
                tags.update(e.tags)
        return tuple(sorted(tags))

    def perceived_target_tags(self, track):
        tags=getattr(track,"perceived_tags",None)
        if tags:
            return {str(x).upper() for x in tags}
        cls=str(getattr(track,"classification","UNKNOWN") or "UNKNOWN").upper()
        table=dict(DEFAULT_CLASSIFICATION_TARGET_TAGS)
        table.update(dict((getattr(self.sim,"targeting_doctrine",{}) or {}).get("classification_target_tags",{})))
        implied=table.get(cls)
        return {str(x).upper() for x in implied} if implied else None

    @staticmethod
    def track_indicates_armor(track) -> bool:
        """Perceived armor formation (classification only; never the target's true branch)."""
        return str(getattr(track,"classification","UNKNOWN") or "").upper()=="ARMOR"

    def weapon_can_affect_perceived(self, weapon, track) -> bool:
        """Decision-layer compatibility from what the shooter believes the target contains."""
        tags=self.perceived_target_tags(track)
        if tags is None:
            return True
        return bool(tags & {str(x).upper() for x in weapon.target_tags})

    def weapon_has_targeting_solution(self, weapon, track, target: Unit) -> bool:
        """Weapon-specific terminal targeting gate layered on top of an actionable Track.

        Unguided direct-fire weapons only need a usable local firing Track/LOS.  Lock-on weapons
        may additionally require a sufficiently classified/signature-compatible target.  The
        rule is data-driven so future thermal/IIR/radar/laser-guided systems can select their own
        lockable target tags without hard-coding weapon names in the engine.
        """
        md=dict(getattr(weapon,"metadata",{}) or {})
        if not bool(md.get("requires_target_lock",False)):
            return True
        allowed_states={str(x).upper() for x in md.get("lock_required_states",["CLASSIFIED","IDENTIFIED"])}
        if str(getattr(track,"state","DETECTED")).upper() not in allowed_states:
            return False
        if float(getattr(track,"confidence",0.0)) < float(md.get("lock_min_confidence",0.52)):
            return False
        lock_tags={str(x).upper() for x in md.get("lockable_target_tags",[])}
        if lock_tags:
            # The seeker locks onto what the shooter perceives, not the live inventory.
            perceived=self.perceived_target_tags(track)
            if perceived is not None and not (perceived & lock_tags):
                return False
        return True

    def unit_can_affect(self, shooter: Unit, target: Unit, mode: str = "DIRECT") -> bool:
        tr = self.sim._track_for(shooter, target, mode)
        if tr is None:
            return False
        perceived_d = math.dist(shooter.pos, tr.estimated_pos)
        if str(mode).upper() in ("COUNTER_BATTERY","FIRE_SUPPORT","INDIRECT_FIRE"):
            # Indirect-fire target eligibility must be perception-driven.  Do not inspect the
            # ground-truth target component inventory to decide whether a believed coordinate is
            # worth firing on; otherwise destruction would be known instantaneously through FoW.
            return any(self.sim.fire_control.eligible(shooter,w,tr,mode) and w.capability.upper()=="INDIRECT_FIRE"
                       for _, w in shooter.operational_weapons())
        # A nominal weapon envelope is not yet a firing solution.  Direct fire also needs a
        # physically usable lane to the perceived target position.  This keeps tactical maneuver
        # consistent with the final fire_weapon() terrain gate: a unit with a Track but a forest-
        # blocked lane must maneuver rather than stopping merely because the target is in range.
        terrain=getattr(self.sim,"terrain",None)
        if str(mode).upper()=="DIRECT" and terrain is not None and hasattr(terrain,"direct_fire_modifier"):
            lane=terrain.direct_fire_modifier(shooter.pos, tr.estimated_pos)
            if not bool(lane.get("allowed",True)):
                return False
        return any(
            w.capability.upper() != "INDIRECT_FIRE" and perceived_d <= w.range_m and self.weapon_can_affect_perceived(w, tr)
            and self.weapon_has_targeting_solution(w,tr,target)
            for _, w in shooter.operational_weapons()
        )

    def target_score(self, shooter: Unit, target: Unit, mode: str = "DIRECT") -> float:
        tr = self.sim._track_for(shooter, target, mode)
        if tr is None:
            return 0.0
        doctrine=dict(getattr(self.sim,"targeting_doctrine",{}) or {})
        cls=str(tr.classification or "UNKNOWN").upper()
        mode=str(mode).upper()
        if mode in ("COUNTER_BATTERY","FIRE_SUPPORT","INDIRECT_FIRE"):
            table=doctrine.get("indirect_fire_priority",{})
            pri=float(table.get(cls,table.get("UNKNOWN",1.0)))
            if mode=="FIRE_SUPPORT" and target.uid in shooter.metadata.get("_fire_support_engaged_ids",[]):
                pri*=max(1.0,float(doctrine.get("engaged_fire_support_priority_multiplier",1.25)))
        else:
            table=doctrine.get("direct_fire_priority",{})
            shooter_table=table.get(shooter.branch,{})
            pri=float(shooter_table.get(cls,shooter_table.get("UNKNOWN",1.0)))
            # Backward compatibility for scenarios that still override the older table.
            if not table:
                pri=float(self.sim.combat_config.get("target_priority",{}).get(shooter.branch,{}).get(cls,1.0))
        source_w=float(doctrine.get("track_source_weight",{}).get(str(tr.source).upper(),1.0))
        d=max(math.dist(shooter.pos,tr.estimated_pos),1.0)
        confidence=max(.15,tr.confidence,tr.belief_confidence*0.75)
        # Selection uses only perceived information. Ground-truth target loss/branch state is
        # deliberately excluded; physical compatibility is checked separately at fire resolution.
        return pri*source_w*confidence/(0.40+d/700.0)

    def select_target(self, shooter: Unit, enemies, mode: str = "DIRECT"):
        """Select a target with persistence/hysteresis instead of re-rolling every combat tick.

        Default behavior:
        - keep the current target while it remains actionable/effective;
        - do not switch during a minimum lock interval;
        - after that, switch only if another target is materially better;
        - immediately switch if the current target is destroyed/lost/out of effective envelope.

        Doctrine can later force a switch by setting `metadata["force_target_id"]` or changing
        the score/threshold configuration.
        """
        cand = [e for e in enemies if self.unit_can_affect(shooter, e, mode)]
        if not cand:
            shooter.target_id = None
            shooter.metadata.pop("target_acquired_t", None)
            return None

        by_id = {e.uid:e for e in cand}
        forced = shooter.metadata.pop("force_target_id", None)
        if forced in by_id:
            shooter.target_id = forced
            shooter.metadata["target_acquired_t"] = self.sim.time
            self.sim.log("TARGET_SWITCH", unit=shooter.uid, target=forced, reason="DOCTRINE_FORCED")
            return by_id[forced]

        current = by_id.get(shooter.target_id)
        if current is None:
            # Deterministic best-score acquisition avoids gratuitous random target churn.
            chosen = max(cand, key=lambda e:self.target_score(shooter,e,mode))
            shooter.target_id = chosen.uid
            shooter.metadata["target_acquired_t"] = self.sim.time
            self.sim.log("TARGET_ACQUIRED", unit=shooter.uid, target=chosen.uid,
                         score=round(self.target_score(shooter,chosen,mode),4), mode=mode)
            return chosen

        acquired = float(shooter.metadata.get("target_acquired_t", self.sim.time))
        dwell = self.sim.time - acquired
        if str(mode).upper() in ("COUNTER_BATTERY","FIRE_SUPPORT","INDIRECT_FIRE"):
            min_lock=float(getattr(self.sim,"targeting_doctrine",{}).get("indirect_target_lock_min_s",12.0))
        else:
            min_lock=float(self.sim.combat_config.get("target_lock_min_s",18.0))
        if dwell < min_lock:
            return current

        current_score = max(1e-6, self.target_score(shooter,current,mode))
        best = max(cand, key=lambda e:self.target_score(shooter,e,mode))
        best_score = self.target_score(shooter,best,mode)
        if str(mode).upper() in ("COUNTER_BATTERY","FIRE_SUPPORT","INDIRECT_FIRE"):
            ratio=float(getattr(self.sim,"targeting_doctrine",{}).get("indirect_target_switch_score_ratio",1.25))
        else:
            ratio=float(self.sim.combat_config.get("target_switch_score_ratio",1.55))

        if best.uid != current.uid and best_score >= current_score * ratio:
            old = current.uid
            shooter.target_id = best.uid
            shooter.metadata["target_acquired_t"] = self.sim.time
            self.sim.log("TARGET_SWITCH", unit=shooter.uid, old_target=old, target=best.uid,
                         reason="SUPERIOR_TARGET", score_ratio=round(best_score/current_score,2))
            return best
        return current


    def _mission_target_id(self, shooter: Unit):
        """Return the explicit entity mission target, if any.

        This is a planning/doctrine preference only.  It never grants a Track or live position;
        local fire allocation still requires the shooter's own actionable perception.
        """
        o=getattr(shooter,"current_order",None)
        if o and str(o.kind).upper() in ("ATTACK_UNIT","DESTROY_UNIT"):
            tid=str(o.params.get("target_unit","") or "")
            return tid or None
        return None

    def _weapon_target_candidates(self, shooter: Unit, source_element, weapon, enemies):
        """Actionable direct-fire targets for one firing element/weapon stream.

        The candidate gate remains perception driven.  Physical weapon/target compatibility is
        evaluated by the combat resolver, while target position comes only from the shooter's
        Track rather than enemy ground truth.
        """
        out=[]
        for target in enemies:
            tr=self.sim._track_for(shooter,target,"DIRECT")
            if tr is None:
                continue
            perceived_d=math.dist(shooter.pos,tr.estimated_pos)
            if perceived_d > weapon.range_m:
                continue
            if not self.weapon_can_affect_perceived(weapon,tr):
                continue
            if not self.weapon_has_targeting_solution(weapon,tr,target):
                continue
            out.append(target)
        return out

    def local_target_score(self, shooter: Unit, source_element, weapon, target: Unit,
                           assigned_streams=None) -> float:
        """Score a target for one direct-fire weapon stream.

        Formation-level BML remains a mission preference, not a command that every subordinate
        firing element must shoot the same formation.  A diminishing-return term spreads fire
        when several equally actionable enemy formations are present.
        """
        score=self.target_score(shooter,target,"DIRECT")
        if score <= 0.0:
            return 0.0
        mission_target=self._mission_target_id(shooter)
        if mission_target and target.uid==mission_target:
            score*=float(self.sim.combat_config.get("local_fire_mission_target_bonus",1.35))
        assigned_streams=assigned_streams or {}
        assigned=float(assigned_streams.get(target.uid,0.0))
        sat=float(self.sim.combat_config.get("local_fire_saturation_penalty",0.65))
        score/=1.0 + sat*assigned
        return score

    def select_local_target(self, shooter: Unit, source_element, weapon, enemies, assigned_streams=None):
        """Select a target independently for a FormationElement/weapon stream.

        Locks are element/weapon scoped so a battalion may simultaneously engage several enemy
        formations without gratuitous per-tick target churn.
        """
        cand=self._weapon_target_candidates(shooter,source_element,weapon,enemies)
        key=f"{source_element.eid}:{weapon.name}"
        locks=shooter.metadata.setdefault("_local_direct_target_locks",{})
        if not cand:
            locks.pop(key,None)
            return None
        by_id={e.uid:e for e in cand}
        st=locks.get(key)
        current=by_id.get(st.get("target_id")) if isinstance(st,dict) else None
        min_lock=float(self.sim.combat_config.get("local_target_lock_min_s",
                                                   self.sim.combat_config.get("target_lock_min_s",18.0)))
        switch_ratio=float(self.sim.combat_config.get("local_target_switch_score_ratio",1.25))
        if current is not None:
            acquired=float(st.get("acquired_t",self.sim.time))
            if self.sim.time-acquired < min_lock:
                return current
            cur_score=max(1e-9,self.local_target_score(shooter,source_element,weapon,current,assigned_streams))
            best=max(cand,key=lambda e:self.local_target_score(shooter,source_element,weapon,e,assigned_streams))
            best_score=self.local_target_score(shooter,source_element,weapon,best,assigned_streams)
            if best.uid==current.uid or best_score < cur_score*switch_ratio:
                return current
            locks[key]={"target_id":best.uid,"acquired_t":self.sim.time}
            self.sim.log("LOCAL_TARGET_SWITCH",unit=shooter.uid,source_element=source_element.eid,
                         weapon=weapon.name,old_target=current.uid,target=best.uid)
            return best
        chosen=max(cand,key=lambda e:self.local_target_score(shooter,source_element,weapon,e,assigned_streams))
        locks[key]={"target_id":chosen.uid,"acquired_t":self.sim.time}
        self.sim.log("LOCAL_TARGET_ACQUIRED",unit=shooter.uid,source_element=source_element.eid,
                     weapon=weapon.name,target=chosen.uid)
        return chosen


    @staticmethod
    def _is_close_awareness_track(track) -> bool:
        return bool(track and str(getattr(track, "observation_zone", "UNKNOWN")).upper()=="CLOSE")

    def close_direct_targets(self, shooter: Unit, enemies):
        """Return actionable enemy formations locally acquired in all-round close awareness.

        This is deliberately based on the shooter's Track metadata, not enemy ground truth.
        Shared C2 tracks and forward-sector tracks do not grant decentralized close-combat fire.
        """
        out=[]
        for target in enemies:
            tr=self.sim._track_for(shooter,target,"DIRECT")
            if tr is not None and self._is_close_awareness_track(tr) and self.unit_can_affect(shooter,target,"DIRECT"):
                out.append(target)
        return out

    def fire_hybrid(self, shooter: Unit, enemies, primary_target: Unit | None, assigned_streams=None):
        """Hybrid direct-fire allocation based on the observer's perceptual geometry.

        CLOSE/all-round contacts represent immediate local threats. Each weapon stream that can
        affect at least one CLOSE contact may independently select among those close targets. A
        weapon stream that cannot affect any CLOSE contact remains available to the formation's
        principal forward target (for example, an AT team can stay on a distant tank while rifle
        elements react to close infantry). Outside CLOSE awareness all streams concentrate on the
        formation-level primary target, preserving coherent frontal/long-range fire.
        """
        assigned_streams = assigned_streams if assigned_streams is not None else {}
        close_targets=(self.close_direct_targets(shooter,enemies)
                       if bool(self.sim.combat_config.get("close_multi_target_fire_enabled",True)) else [])
        fired=[]
        if not close_targets:
            if primary_target is None:
                return fired
            self.fire_all(shooter,primary_target,"DIRECT")
            return [primary_target.uid]

        for element,weapon in shooter.operational_weapons():
            if weapon.capability.upper()=="INDIRECT_FIRE":
                continue
            close_cand=self._weapon_target_candidates(shooter,element,weapon,close_targets)
            if close_cand:
                target=self.select_local_target(shooter,element,weapon,close_targets,assigned_streams)
                if target is not None:
                    assigned_streams[target.uid]=float(assigned_streams.get(target.uid,0.0))+1.0
                    fired.append(target.uid)
                    self.fire_weapon(shooter,target,element,weapon,"DIRECT")
                continue
            if primary_target is not None:
                tr=self.sim._track_for(shooter,primary_target,"DIRECT")
                if (tr is not None and math.dist(shooter.pos,tr.estimated_pos)<=weapon.range_m
                        and self.weapon_can_affect_perceived(weapon,tr)
                        and self.weapon_has_targeting_solution(weapon,tr,primary_target)):
                    fired.append(primary_target.uid)
                    self.fire_weapon(shooter,primary_target,element,weapon,"DIRECT")
        return fired

    def select_target_element(self, target: Unit, weapon):
        tags = {x.upper() for x in weapon.target_tags}
        cand = [e for e in target.elements.values() if e.alive and target.element_exposed(e) and e.tags & tags]
        if not cand:
            return None
        return self.sim.rng.choices(cand, weights=[max(0.05, e.count) for e in cand], k=1)[0]

    def fire_weapon(self, shooter: Unit, target: Unit, source_element, weapon, mode="DIRECT"):
        power = shooter.firepower(source_element, weapon)
        key = f"{source_element.eid}:{weapon.name}"
        state = shooter.metadata.get('_direct_fire_cycle_state', {}).get(key)
        if power.participants <= 0:
            if state is not None:
                state['unavailable'] = True
            return
        if weapon.capability.upper() == "INDIRECT_FIRE":
            return self.sim.indirect_fire.fire_mission(shooter, target, source_element, weapon, mode)
        if state and state.pop('unavailable', False):
            state['next_ready_at'] = self.sim.time
        if state and state.get('participants', power.participants) != power.participants:
            remaining = max(0.0, state['next_ready_at'] - self.sim.time)
            state['next_ready_at'] = self.sim.time + remaining * state['participants'] / power.participants
        if state:
            state['participants'] = power.participants
        # Resolve every due opportunity, including multiple shots within one tick.
        # This avoids the former one-shot-per-tick cap at large formation sizes.
        while self._fire_weapon_once(shooter, target, source_element, weapon, mode):
            pass
        state = shooter.metadata.get('_direct_fire_cycle_state', {}).get(key)
        if state:
            # Lost LOS/acquisition/ammo must not bank a backlog of free shots.
            state['next_ready_at'] = max(state['next_ready_at'], self.sim.time)

    def _fire_weapon_once(self, shooter: Unit, target: Unit, source_element, weapon, mode="DIRECT"):
        power = shooter.firepower(source_element, weapon)
        if power.participants <= 0:
            return False
        if weapon.capability.upper() == "INDIRECT_FIRE":
            return self.sim.indirect_fire.fire_mission(shooter, target, source_element, weapon, mode)

        tr = self.sim._track_for(shooter, target, mode)
        if tr is None:
            return
        perceived_d = math.dist(shooter.pos, tr.estimated_pos)
        if perceived_d > weapon.range_m or not self.weapon_can_affect_perceived(weapon, tr):
            return
        if not self.weapon_has_targeting_solution(weapon,tr,target):
            self.sim.log("DIRECT_FIRE_NO_TARGETING_SOLUTION",shooter=shooter.uid,target=target.uid,
                         weapon=weapon.name,track_state=tr.state,track_confidence=round(tr.confidence,2))
            return

        # A remembered LOCAL track is not permission to instantaneously slew the whole formation's
        # principal observation / engagement sector. Outside the short all-round CLOSE zone, direct
        # fire waits until watch_heading_deg has physically slewed far enough for the target to enter
        # the current forward sector. Weapon-level acquisition/lay delay then applies as a separate
        # fire-control timing stage.
        if str(mode).upper()=="DIRECT" and bool(self.sim.combat_config.get("direct_fire_requires_watch_alignment",True)):
            close_zone=str(getattr(tr,"observation_zone","UNKNOWN")).upper()=="CLOSE"
            if not close_zone:
                _,fov,_,_,_=self.sim._visual_sensor_profile(shooter,target)
                # Orientation is judged against where the shooter believes the target is.
                aim=tr.estimated_pos
                bearing=math.degrees(math.atan2(aim[1]-shooter.pos[1],aim[0]-shooter.pos[0]))
                off=abs(self.sim._angle_delta_deg(bearing,shooter.watch_heading_deg))
                margin=max(0.0,float(self.sim.combat_config.get("direct_fire_watch_edge_margin_deg",5.0)))
                if off > max(1.0,fov*0.5-margin):
                    self.sim.log("DIRECT_FIRE_WAIT_ORIENTATION",shooter=shooter.uid,target=target.uid,
                                 weapon=weapon.name,watch_heading_deg=round(shooter.watch_heading_deg,1),
                                 target_bearing_deg=round(bearing%360.0,1),offset_deg=round(off,1))
                    return

        # Re-check the current physical firing lane. Observation and Track memory are not a licence
        # to shoot through dense vegetation after LOS has closed. The ray penalty depends on how
        # much vegetation is actually crossed, not merely on whether shooter/target is tagged FOREST.
        fire_lane={"allowed":True,"effect_factor":1.0,"vegetation_path_m":0.0}
        terrain=getattr(self.sim,"terrain",None)
        if str(mode).upper()=="DIRECT" and terrain is not None and hasattr(terrain,"direct_fire_modifier"):
            fire_lane=terrain.direct_fire_modifier(shooter.pos,target.pos)
            if not bool(fire_lane.get("allowed",True)):
                self.sim.log("DIRECT_FIRE_BLOCKED_TERRAIN",shooter=shooter.uid,target=target.uid,
                             weapon=weapon.name,vegetation_path_m=round(float(fire_lane.get("vegetation_path_m",0.0)),1))
                return

        key = f"{source_element.eid}:{weapon.name}"

        # Direct-fire target acquisition/lay delay.  Previously the first shot could occur in the
        # exact simulation tick in which a usable Track appeared.  Keep the timing generic and
        # data-driven: each weapon may override the default acquisition window in metadata.
        fire_state=shooter.metadata.setdefault("_direct_fire_state",{})
        st=fire_state.get(key)
        local_lock=shooter.metadata.get("_local_direct_target_locks",{}).get(key,{}) if str(mode).upper()=="DIRECT" else {}
        acquired_stamp=float(local_lock.get("acquired_t",shooter.metadata.get("target_acquired_t",self.sim.time)))
        if (st is None or st.get("target_id") != target.uid
                or abs(float(st.get("target_acquired_t",-1e9))-acquired_stamp) > 1e-6):
            lo=float(weapon.metadata.get("acquisition_delay_min_s",
                     self.sim.combat_config.get("direct_fire_acquisition_delay_min_s",2.0)))
            hi=float(weapon.metadata.get("acquisition_delay_max_s",
                     self.sim.combat_config.get("direct_fire_acquisition_delay_max_s",5.0)))
            delay=self.sim.rng.uniform(lo,max(lo,hi))
            # Prepared defenders tend to have weapons laid and crews scanning assigned sectors;
            # moving formations generally need more time to halt/orient/identify before firing.
            state_name=str(getattr(getattr(shooter,"state",None),"value",getattr(shooter,"state",""))).upper()
            if state_name=="DEFENDING": delay*=0.78
            elif state_name in ("MOVING","RETREATING"): delay*=1.25
            fire_state[key]={"target_id":target.uid,"target_acquired_t":acquired_stamp,
                             "ready_at":self.sim.time+delay}
            self.sim.log("DIRECT_FIRE_ACQUIRING",shooter=shooter.uid,target=target.uid,
                         weapon=weapon.name,delay_s=round(delay,1))
            return
        if self.sim.time < float(st.get("ready_at",self.sim.time)):
            return

        # shots_per_min is an effective engagement-cycle rate (aim/reload/burst abstraction),
        # not the mechanical cyclic rate of an individual firearm.
        # Each surviving shooter or fully staffed weapon contributes one such rate.
        active_systems=power.participants

        # Direct-fire cadence is an effective tactical engagement cycle, not mechanical cyclic RPM.
        # Explicit cycle windows can represent burst length, correction, crew drill and weapon reset.
        # The caller resolves all due opportunities, including several in one step.
        cycle_state=shooter.metadata.setdefault("_direct_fire_cycle_state",{})
        cst=cycle_state.setdefault(key,{"cycles":0,"next_ready_at":-1e9})
        if self.sim.time < float(cst.get("next_ready_at",-1e9)):
            return
        cmin=weapon.metadata.get("engagement_cycle_min_s")
        cmax=weapon.metadata.get("engagement_cycle_max_s")
        if cmin is not None or cmax is not None:
            lo=float(cmin if cmin is not None else cmax); hi=float(cmax if cmax is not None else lo)
            interval=max(0.01,self.sim.rng.uniform(lo,max(lo,hi)))/active_systems
        else:
            interval=60.0/max(weapon.shots_per_min*active_systems,.01)

        if not weapon.expend_round():
            return
        shooter.weapon_last_fire[key] = self.sim.time
        cycles=int(cst.get("cycles",0))+1
        cst["cycles"]=cycles
        cycle_origin=self.sim.time if cycles==1 else float(cst['next_ready_at'])
        next_ready=cycle_origin+interval
        cst['participants']=active_systems
        every=max(0,int(weapon.metadata.get("reload_after_cycles",0)))
        if every and cycles % every == 0:
            rlo=float(weapon.metadata.get("reload_delay_min_s",0.0)); rhi=float(weapon.metadata.get("reload_delay_max_s",rlo))
            reload_delay=self.sim.rng.uniform(rlo,max(rlo,rhi))/active_systems
            next_ready+=reload_delay
            self.sim.log("DIRECT_FIRE_RELOAD",shooter=shooter.uid,source_element=source_element.eid,
                         weapon=weapon.name,delay_s=round(reload_delay,1))
        cst["next_ready_at"]=next_ready
        if weapon.ammo_remaining == 0:
            self.sim.log("AMMO_DEPLETED", unit=shooter.uid, source_element=source_element.eid, weapon=weapon.name)

        tgt_el = self.select_target_element(target, weapon)
        if not tgt_el:
            # The round was committed on perceived composition; the target no longer contains
            # anything this weapon can affect (e.g. its exposed dismounts are already gone).
            self.sim.log("FIRE_NO_EFFECT", shooter=shooter.uid, target=target.uid,
                         source_element=source_element.eid, weapon=weapon.name,
                         reason="NO_COMPATIBLE_ELEMENT", ammo_remaining=weapon.ammo_remaining)
            return True

        # Separate geometrical hit probability from post-hit armor effect.  ``weapon.pk`` remains
        # the legacy/default base hit probability, while modern AT weapons can provide calibrated
        # hit-model metadata without changing the downstream damage model.
        base_hit=float(weapon.metadata.get("base_hit_probability",weapon.pk))
        range_min=float(weapon.metadata.get("range_hit_factor_min",0.20))
        range_falloff=float(weapon.metadata.get("range_hit_falloff",0.55))
        range_factor=max(range_min,1.0-range_falloff*(perceived_d/max(weapon.range_m,1)))
        defense_factor=float(weapon.metadata.get("defending_hit_factor",0.72 if target.state==UnitState.DEFENDING else 1.0)) if target.state==UnitState.DEFENDING else 1.0
        targeting_mode=str(weapon.metadata.get("targeting_mode","")).upper()
        if targeting_mode in ("LOCK_ON_BEFORE_LAUNCH","GUIDED","FIRE_AND_FORGET"):
            # Once a seeker-quality lock exists, residual Track uncertainty should degrade the shot
            # much less than it does an unguided weapon.
            conf_floor=float(weapon.metadata.get("guided_track_factor_floor",0.82))
            err_penalty=min(float(weapon.metadata.get("guided_error_penalty_max",0.12)),
                            tr.position_error_m/max(weapon.range_m,1))
            track_factor=max(conf_floor,min(1.0,conf_floor+(1.0-conf_floor)*tr.confidence-err_penalty))
        else:
            track_factor=max(.25,min(1.0,tr.confidence*(1.0-min(.75,tr.position_error_m/max(weapon.range_m,1)))))
        terrain_fire_factor=max(0.0,min(1.0,float(fire_lane.get("effect_factor",1.0))))
        barricade_factor=1.0
        if fire_lane.get("barricade_cover"):
            # Earth-filled MIL1-class barriers strongly reduce exposed small-arms hit opportunity,
            # but heavy direct weapons retain more effect.  This is directional frontal cover,
            # not an omnidirectional armor multiplier.
            cap=str(weapon.capability).upper(); tags={str(x).upper() for x in weapon.target_tags}
            heavy=(cap in {"ANTI_ARMOR","DIRECT_FIRE_HEAVY"} or "STRUCTURE" in tags
                   or float(weapon.metadata.get("caliber_mm",0.0))>=20.0)
            barricade_factor=float(fire_lane.get("barricade_heavy_factor" if heavy else "barricade_small_arms_factor",0.70 if heavy else 0.40))
        hit_p=min(float(weapon.metadata.get("max_hit_probability",0.95)),
                  max(0.0,base_hit*range_factor*defense_factor*track_factor*terrain_fire_factor*barricade_factor))
        hit = self.sim.rng.random() < hit_p
        if str(mode).upper() != "DIRECT":
            shooter.target_id = target.uid

        self.sim.log(
            "FIRE", shooter=shooter.uid, target=target.uid, source_element=source_element.eid,
            weapon=weapon.name, capability=weapon.capability, mode=mode,
            distance=round(perceived_d, 1), track_confidence=round(tr.confidence, 2),
            track_error_m=round(tr.position_error_m, 1), hit=hit, target_element=tgt_el.eid,
            terrain_fire_factor=round(float(fire_lane.get("effect_factor",1.0)),3),
            vegetation_path_m=round(float(fire_lane.get("vegetation_path_m",0.0)),1),
            barricade_cover=fire_lane.get("barricade_cover"), barricade_factor=round(barricade_factor,3),
            ammo_remaining=weapon.ammo_remaining,
            firing_participants=power.participants, available_operators=power.operators,
        )
        self.sim.publish_engagement_contact(shooter,tr)
        self.sim._register_threat_cue(target,shooter.uid,"DIRECT_FIRE")
        if hit:
            # If the target occupies a building, structure-capable weapons may attack the cover
            # itself. This keeps occupants hard to hit without making buildings invulnerable.
            if terrain is not None and hasattr(terrain,"building_at"):
                bld=terrain.building_at(target.pos)
                structure_capable=("STRUCTURE" in {str(x).upper() for x in weapon.target_tags}
                                    or bool(weapon.metadata.get("structure_capable",False)))
                if bld is not None and structure_capable:
                    lo=float(weapon.metadata.get("building_direct_damage_min",18.0)); hi=float(weapon.metadata.get("building_direct_damage_max",32.0))
                    rec=terrain.apply_building_damage(bld,self.sim.rng.uniform(lo,max(lo,hi)),source=shooter.uid,weapon=weapon.name)
                    if rec:
                        self.sim.log("BUILDING_DAMAGE",shooter=shooter.uid,building=rec["building"],weapon=weapon.name,damage=round(rec["damage"],1),integrity=round(rec["integrity"],1))
                        if rec["destroyed"]:
                            self.sim.log("BUILDING_DESTROYED",shooter=shooter.uid,building=rec["building"],weapon=weapon.name)
                            self.sim._handle_destroyed_building(bld,shooter.uid,weapon.name)
            if tgt_el.category.upper()=="EQUIPMENT":
                effect=self.sim.damage.direct_weapon_effect(target,tgt_el,weapon)
                if effect is not None:
                    self.sim.events.push(
                        self.sim.time+.15,"EQUIPMENT_EFFECT",target=target.uid,source=shooter.uid,
                        element=tgt_el.eid,effect=effect,weapon=weapon.name,reason="DIRECT_WEAPON_HIT"
                    )
                else:
                    self.sim.log(
                        "ARMOR_HIT_NO_PERSISTENT_DAMAGE", shooter=shooter.uid,target=target.uid,
                        source_element=source_element.eid,target_element=tgt_el.eid,weapon=weapon.name
                    )
            else:
                loss = 1 if weapon.max_effect_count <= 1 else self.sim.rng.randint(1, weapon.max_effect_count)
                self.sim.events.push(
                    self.sim.time + .15, "ELEMENT_LOSS", target=target.uid, source=shooter.uid,
                    element=tgt_el.eid, count=loss, weapon=weapon.name
                )
        return True

    def fire_structure_weapon(self, shooter:Unit, building, source_element:FormationElement, weapon:WeaponModel):
        key=f"{source_element.eid}:{weapon.name}:STRUCT:{building.get('id')}"
        states=shooter.metadata.setdefault('_structure_fire_cycle_state',{})
        state=states.setdefault(key,{'next_ready_at':self.sim.time,'participants':0})
        power=shooter.firepower(source_element,weapon)
        old=state['participants']
        if old and power.participants and old != power.participants:
            remaining=max(0.0,state['next_ready_at']-self.sim.time)
            state['next_ready_at']=self.sim.time+remaining*old/power.participants
        if not old:
            state['next_ready_at']=self.sim.time
        state['participants']=power.participants
        result=self._fire_structure_weapon_once(shooter,building,source_element,weapon,state)
        handled=bool(result)
        while result == 'FIRED':
            result=self._fire_structure_weapon_once(shooter,building,source_element,weapon,state)
        state['next_ready_at']=max(state['next_ready_at'],self.sim.time)
        return handled

    def _fire_structure_weapon_once(self, shooter, building, source_element, weapon, cycle):
        """Direct attack on a known static structure; does not require an enemy-unit Track."""
        tags={str(x).upper() for x in weapon.target_tags}
        if "STRUCTURE" not in tags and not bool(weapon.metadata.get("structure_capable",False)):return False
        if str(weapon.capability).upper()=="INDIRECT_FIRE":return False
        power=shooter.firepower(source_element,weapon)
        if power.participants<=0:return False
        terrain=getattr(self.sim,"terrain",None)
        if terrain is None or not terrain.building_operational(building):return False
        aim=terrain.building_center(building);d=math.dist(shooter.pos,aim)
        if d>weapon.range_m:return False
        lane=terrain.direct_fire_modifier(shooter.pos,aim) if hasattr(terrain,"direct_fire_modifier") else {"allowed":True,"effect_factor":1.0}
        # The target building itself is not treated as an intervening blocker by direct_fire_modifier
        # when the aim point lies inside it; third-party terrain still can block the shot.
        if not bool(lane.get("allowed",True)):return False
        key=f"{source_element.eid}:{weapon.name}:STRUCT:{building.get('id')}";st=shooter.metadata.setdefault("_direct_fire_state",{}).get(key)
        if st is None:
            lo=float(weapon.metadata.get("acquisition_delay_min_s",1.5));hi=float(weapon.metadata.get("acquisition_delay_max_s",4.0))
            shooter.metadata["_direct_fire_state"][key]={"ready_at":self.sim.time+self.sim.rng.uniform(lo,max(lo,hi))}
            return True
        if self.sim.time<float(st.get("ready_at",self.sim.time)):return True
        if self.sim.time < cycle['next_ready_at']:return True
        cmin=weapon.metadata.get('engagement_cycle_min_s')
        cmax=weapon.metadata.get('engagement_cycle_max_s')
        if cmin is not None or cmax is not None:
            lo=float(cmin if cmin is not None else cmax)
            hi=float(cmax if cmax is not None else lo)
            interval=max(.01,self.sim.rng.uniform(lo,max(lo,hi)))/power.participants
        else:
            interval=60.0/max(0.01,weapon.shots_per_min*power.participants)
        if not weapon.expend_round():return False
        cycle['next_ready_at']+=interval
        shooter.weapon_last_fire[key]=self.sim.time
        range_factor=max(.25,1.0-.45*(d/max(1.0,weapon.range_m)))
        hit_p=min(.95,max(.02,weapon.pk*1.35*range_factor*float(lane.get("effect_factor",1.0))))
        hit=self.sim.rng.random()<hit_p
        self.sim.log("FIRE_STRUCTURE",shooter=shooter.uid,building=str(building.get("id")),weapon=weapon.name,distance=round(d,1),hit=hit,ammo_remaining=weapon.ammo_remaining)
        if hit:
            lo=float(weapon.metadata.get("building_direct_damage_min",18.0));hi=float(weapon.metadata.get("building_direct_damage_max",32.0))
            rec=terrain.apply_building_damage(building,self.sim.rng.uniform(lo,max(lo,hi)),source=shooter.uid,weapon=weapon.name)
            if rec:
                self.sim.log("BUILDING_DAMAGE",shooter=shooter.uid,building=rec["building"],weapon=weapon.name,damage=round(rec["damage"],1),integrity=round(rec["integrity"],1))
                if rec["destroyed"]:
                    self.sim.log("BUILDING_DESTROYED",shooter=shooter.uid,building=rec["building"],weapon=weapon.name)
                    self.sim._handle_destroyed_building(building,shooter.uid,weapon.name)
        return 'FIRED'

    def fire_all(self, shooter: Unit, target: Unit, mode="DIRECT"):
        for element, weapon in shooter.operational_weapons():
            if str(mode).upper()=="DIRECT" and weapon.capability.upper()=="INDIRECT_FIRE":
                continue
            self.fire_weapon(shooter, target, element, weapon, mode)

    def fire_local(self, shooter: Unit, enemies, assigned_streams=None):
        """Resolve direct fire with element/weapon-level target allocation.

        Total weapon streams and their calibrated fire cycles are unchanged; only target
        allocation is decentralized.  ``assigned_streams`` may be shared by friendly formations
        inside one local engagement to discourage pathological pile-on against a single target.
        """
        assigned_streams = assigned_streams if assigned_streams is not None else {}
        fired_targets=[]
        for element, weapon in shooter.operational_weapons():
            if weapon.capability.upper()=="INDIRECT_FIRE":
                continue
            target=self.select_local_target(shooter,element,weapon,enemies,assigned_streams)
            if target is None:
                continue
            assigned_streams[target.uid]=float(assigned_streams.get(target.uid,0.0))+1.0
            fired_targets.append(target.uid)
            self.fire_weapon(shooter,target,element,weapon,"DIRECT")
        return fired_targets
