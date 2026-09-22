
from __future__ import annotations
from typing import Dict, Tuple
import math


class FireControlEngine:
    """Delayed indirect-fire command-and-control pipeline.

    It freezes the firing coordinate when a fire mission is requested. Later target movement or
    sensor updates do not magically revise that mission. A new report/new mission is required to
    use a newer coordinate.

    Stages represented by configurable delays:
      observer/C2 sharing happens upstream in Simulation,
      FIRE SUPPORT REQUEST -> FDC/fire-direction computation -> gun preparation/loading -> FIRE,
      then projectile time-of-flight -> IMPACT.

    The default numbers are generic open M&S timing assumptions, not specifications for a
    particular military unit or weapon.
    """

    def __init__(self, sim):
        self.sim = sim
        # weapon-key -> active pending mission descriptor. Event-queue entries are not
        # physically deleted on preemption; a mission token makes superseded events harmless.
        self.pending: Dict[Tuple[str,str,str], dict] = {}
        self.next_available: Dict[Tuple[str,str], float] = {}
        self.last_fired: Dict[Tuple[str,str], dict] = {}
        self._mission_seq = 0

    def _uniform(self, lo_key: str, hi_key: str) -> float:
        c=self.sim.combat_config
        return self.sim.rng.uniform(float(c.get(lo_key,0.0)),float(c.get(hi_key,c.get(lo_key,0.0))))

    def eligible(self, shooter, weapon, track, mode):
        if weapon.capability.upper() != "INDIRECT_FIRE":return False
        if not shooter.metadata.get("indirect_fire_authorized",True):return False
        allowed=weapon.metadata.get("allowed_indirect_target_classes")
        if allowed is not None and track.classification.upper() not in {str(x).upper() for x in allowed}:return False
        distance=math.dist(shooter.pos,track.estimated_pos)
        return max(0.0,float(weapon.metadata.get("min_range_m",0))) <= distance <= weapon.range_m

    def request(self, shooter, target, source_element, weapon, mode="FIRE_SUPPORT") -> bool:
        if shooter.firepower(source_element, weapon).participants <= 0:
            return False
        tr=self.sim._track_for(shooter,target,mode)
        if tr is None or not self.eligible(shooter,weapon,tr,mode):
            return False
        key=(shooter.uid,source_element.eid,weapon.name)
        weapon_key=(shooter.uid,f"{source_element.eid}:{weapon.name}")
        priorities=self.sim.combat_config.get("fire_mission_priority",{})
        new_priority=int(priorities.get(mode,10))
        active=self.pending.get(key)
        if active is not None:
            old_priority=int(active.get("priority",10))
            allow_preempt=(mode=="COUNTER_BATTERY"
                           and bool(self.sim.combat_config.get("counter_battery_preempts_fire_support",True))
                           and new_priority>old_priority)
            if not allow_preempt:
                return False
            self.sim.log("FIRE_MISSION_PREEMPTED",shooter=shooter.uid,
                         old_target=active.get("target"),old_mode=active.get("mode"),
                         new_target=target.uid,new_mode=mode)
        if self.sim.time < self.next_available.get(weapon_key,-1e9):
            return False

        # Snapshot the information available at the moment the request is initiated.
        aim=tuple(tr.estimated_pos)
        obs_time=float(tr.last_seen_time)
        report_age=max(0.0,self.sim.time-obs_time)

        # Initial missions pay the full observer/FDC/gun-preparation latency.  A repeat mission on
        # the same target within a short window is an adjustment/follow-on mission and therefore
        # uses shorter, separately configurable delays; it still cannot violate the weapon cycle.
        last=self.last_fired.get(weapon_key)
        repeat_window=float(self.sim.combat_config.get("repeat_fire_mission_window_s",180.0))
        repeat=bool(last and last.get("target")==target.uid and last.get("mode")==mode
                    and self.sim.time-float(last.get("time",-1e9)) <= repeat_window)
        if repeat:
            request_delay=self._uniform("repeat_fire_support_request_delay_min_s","repeat_fire_support_request_delay_max_s")
            fdc_delay=self._uniform("repeat_fire_direction_compute_delay_min_s","repeat_fire_direction_compute_delay_max_s")
            gun_delay=self._uniform("repeat_gun_prepare_delay_min_s","repeat_gun_prepare_delay_max_s")
        else:
            request_delay=self._uniform("fire_support_request_delay_min_s","fire_support_request_delay_max_s")
            fdc_delay=self._uniform("fire_direction_compute_delay_min_s","fire_direction_compute_delay_max_s")
            gun_delay=self._uniform("gun_prepare_delay_min_s","gun_prepare_delay_max_s")
        total=request_delay+fdc_delay+gun_delay

        self._mission_seq += 1
        token=self._mission_seq
        self.pending[key]={"token":token,"priority":new_priority,"target":target.uid,"mode":mode}
        self.sim.events.push(
            self.sim.time+total,"FIRE_MISSION_READY",
            shooter=shooter.uid,target=target.uid,source_element=source_element.eid,weapon=weapon.name,
            mode=mode,aim=aim,track_error_m=float(tr.position_error_m),
            track_confidence=float(tr.confidence),observation_time=obs_time,
            request_time=self.sim.time,report_age_at_request_s=report_age,pending_key=key,
            mission_token=token,mission_priority=new_priority,
        )
        self.sim.log(
            "FIRE_MISSION_REQUEST",shooter=shooter.uid,target=target.uid,weapon=weapon.name,mode=mode,
            aim=[round(aim[0],1),round(aim[1],1)],track_age_s=round(report_age,1),
            request_delay_s=round(request_delay,1),fdc_delay_s=round(fdc_delay,1),
            gun_prepare_delay_s=round(gun_delay,1),total_to_fire_s=round(total,1),repeat_mission=repeat,
        )
        return True


    def request_coordinate(self, shooter, source_element, weapon, aim, target_ref:str, mode="INFRASTRUCTURE_STRIKE", position_error_m:float=3.0) -> bool:
        """Request a known-coordinate indirect-fire mission without fabricating an enemy Track."""
        if shooter.firepower(source_element, weapon).participants <= 0:
            return False
        key=(shooter.uid,source_element.eid,weapon.name)
        weapon_key=(shooter.uid,f"{source_element.eid}:{weapon.name}")
        priorities=self.sim.combat_config.get("fire_mission_priority",{})
        new_priority=int(priorities.get(mode,25))
        active=self.pending.get(key)
        if active is not None: return False
        if self.sim.time < self.next_available.get(weapon_key,-1e9): return False
        aim=tuple(aim)
        if not shooter.metadata.get("indirect_fire_authorized",True):return False
        if not max(0.0,float(weapon.metadata.get("min_range_m",0))) <= math.dist(shooter.pos,aim) <= weapon.range_m:return False
        last=self.last_fired.get(weapon_key)
        repeat_window=float(self.sim.combat_config.get("repeat_fire_mission_window_s",180.0))
        repeat=bool(last and last.get("target")==target_ref and last.get("mode")==mode and self.sim.time-float(last.get("time",-1e9))<=repeat_window)
        if repeat:
            request_delay=self._uniform("repeat_fire_support_request_delay_min_s","repeat_fire_support_request_delay_max_s")
            fdc_delay=self._uniform("repeat_fire_direction_compute_delay_min_s","repeat_fire_direction_compute_delay_max_s")
            gun_delay=self._uniform("repeat_gun_prepare_delay_min_s","repeat_gun_prepare_delay_max_s")
        else:
            request_delay=self._uniform("fire_support_request_delay_min_s","fire_support_request_delay_max_s")
            fdc_delay=self._uniform("fire_direction_compute_delay_min_s","fire_direction_compute_delay_max_s")
            gun_delay=self._uniform("gun_prepare_delay_min_s","gun_prepare_delay_max_s")
        total=request_delay+fdc_delay+gun_delay
        self._mission_seq+=1; token=self._mission_seq
        self.pending[key]={"token":token,"priority":new_priority,"target":target_ref,"mode":mode}
        self.sim.events.push(self.sim.time+total,"FIRE_MISSION_READY",shooter=shooter.uid,target=target_ref,source_element=source_element.eid,weapon=weapon.name,mode=mode,aim=aim,track_error_m=float(position_error_m),track_confidence=1.0,observation_time=self.sim.time,request_time=self.sim.time,report_age_at_request_s=0.0,pending_key=key,mission_token=token,mission_priority=new_priority)
        self.sim.log("FIRE_MISSION_REQUEST",shooter=shooter.uid,target=target_ref,weapon=weapon.name,mode=mode,aim=[round(aim[0],1),round(aim[1],1)],track_age_s=0.0,request_delay_s=round(request_delay,1),fdc_delay_s=round(fdc_delay,1),gun_prepare_delay_s=round(gun_delay,1),total_to_fire_s=round(total,1),repeat_mission=repeat)
        return True

    def _find_weapon(self, shooter, element_id: str, weapon_name: str):
        el=shooter.elements.get(element_id)
        if not el or not el.operational:
            return None,None
        for w in el.weapons:
            if w.name==weapon_name and shooter.firepower(el,w).participants > 0:
                return el,w
        return el,None

    def on_ready(self,payload):
        key=tuple(payload.get("pending_key",()))
        active=self.pending.get(key)
        # Superseded/preempted mission event: ignore it when it eventually leaves the queue.
        if active is None or int(active.get("token",-1)) != int(payload.get("mission_token",-2)):
            return
        shooter=self.sim.units.get(payload.get("shooter"))
        if not shooter or not shooter.alive:
            self.pending.pop(key,None)
            return
        el,w=self._find_weapon(shooter,payload["source_element"],payload["weapon"])
        if not el or not w:
            self.pending.pop(key,None)
            self.sim.log("FIRE_MISSION_ABORTED",shooter=shooter.uid,weapon=payload.get("weapon"),
                         reason="WEAPON_OR_CREW_UNAVAILABLE")
            return

        weapon_key=(shooter.uid,f"{el.eid}:{w.name}")
        reload_delay=self._uniform("artillery_reload_delay_min_s","artillery_reload_delay_max_s")
        cycle_floor=60.0/max(float(w.shots_per_min),0.01)
        cycle_delay=max(reload_delay,cycle_floor)
        self.next_available[weapon_key]=self.sim.time+cycle_delay
        self.last_fired[weapon_key]={"target":payload.get("target"),"mode":payload.get("mode","FIRE_SUPPORT"),"time":self.sim.time}
        self.pending.pop(key,None)

        self.sim.indirect_fire.launch_prepared_mission(
            shooter=shooter,source_element=el,weapon=w,target_id=payload["target"],
            aim=tuple(payload["aim"]),track_error_m=float(payload["track_error_m"]),
            track_confidence=float(payload["track_confidence"]),observation_time=float(payload["observation_time"]),
            mode=payload.get("mode","FIRE_SUPPORT"),request_time=float(payload.get("request_time",self.sim.time)),
        )
