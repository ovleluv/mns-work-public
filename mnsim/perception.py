"""Perception subsystem of :class:`mnsim.simulation.Simulation` (mixin).

Visual sensing and watch orientation, threat/shared cues, counter-battery radar, track
actionability, C2 track reception and battle-damage assessment.  Split out of simulation.py
without changing behaviour; methods operate on the Simulation instance (``self``).
"""
from __future__ import annotations

import math
from dataclasses import replace

from .model import FormationElement, Side, Track, Unit, UnitState


class PerceptionMixin:
    DEFAULT_BRANCH_SIGNATURE = {"INFANTRY":0.72, "MECH_INFANTRY":1.05, "MOTORIZED_INFANTRY":0.95,
                                "RECON":0.60, "SPECIAL_OPERATIONS":0.55, "ARMOR":1.25, "ARTILLERY":1.00}

    def _target_signature(self, target: Unit) -> float:
        cfg=dict(self.combat_config.get("target_signature",{}) or {})
        table=dict(self.DEFAULT_BRANCH_SIGNATURE); table.update(dict(cfg.get("branch",{})))
        base = float(table.get(target.branch, cfg.get("default",0.85)))
        if target.state == UnitState.DEFENDING:
            base *= 0.82
        if target.state in (UnitState.MOVING, UnitState.ATTACKING, UnitState.RETREATING):
            base *= 1.08
        # Bigger formations present more to see (reference = a ~30-strong platoon).
        ref=max(1.0,float(cfg.get("reference_size",30.0)))
        size=max(1.0,target.personnel+4.0*target.equipment)
        base *= max(float(cfg.get("size_factor_min",0.6)),min(float(cfg.get("size_factor_max",1.8)),(size/ref)**0.25))
        # Firing gives a unit away (muzzle flash, dust, noise) for a short time.
        last_fire=max(target.weapon_last_fire.values(),default=-1e9)
        if self.time-last_fire<=float(cfg.get("firing_window_s",10.0)):
            base *= float(cfg.get("firing_factor",1.6))
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
        # A large formation observes from its whole footprint, not from its centroid: extend the
        # all-round and forward envelopes by the footprint half-extent (a squad adds ~nothing,
        # a battalion several hundred metres).
        if bool(self.combat_config.get("observation_scales_with_footprint",True)):
            ext=self._footprint_half_extent(unit)
            close+=ext; forward+=ext
        return sensor_mode,forward,fov,close,slew

    def _footprint_half_extent(self, unit: Unit) -> float:
        cached=unit.metadata.get("_obs_extent")
        key=(round(self.time,3),unit.echelon,str(unit.metadata.get("dispersion_posture","")),round(unit.strength_ratio,2))
        if cached and cached[0]==key:
            return cached[1]
        from .formation_geometry import footprint_for
        fp=footprint_for(self,unit)
        # Only growth beyond the echelon-1 (platoon/vehicle) footprint counts, so platoon and
        # single-vehicle behaviour is unchanged.
        scale_cfg=self.combat_config.get("formation_echelon_footprint_scale",{})
        scale=max(1.0,float(unit.metadata.get("formation_echelon_scale",scale_cfg.get(str(unit.echelon).upper(),1.0))))
        ext=0.5*max(fp.length_m,fp.width_m)*(1.0-1.0/scale)
        unit.metadata["_obs_extent"]=(key,ext)
        return ext

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
        key=(self.terrain.revision() if self.terrain else None, repr(self.combat_config.get("environment",{})))
        if getattr(self,"_sensor_boost_key",None)==key:
            return
        self._sensor_boost_key=key
        bound=1.0
        zones=list(self.terrain.data.get("observation_zones",[])) if self.terrain else []
        zones+=list(self.terrain.areas) if self.terrain else []
        for z in zones:
            raw=dict(z.get("observation_modifier",{}))
            for ov in dict(z.get("sensor_overrides",{})).values():
                raw.update(dict(ov))
            for k in ("range_factor","awareness_factor","fov_factor"):
                bound=max(bound,float(raw.get(k,1.0)))
            if str(z.get("type","")).upper()=="BUILDING":
                bound=max(bound,float(z.get("external_range_factor",0.82)))
        env=dict(self.combat_config.get("environment",{}))
        for table in ("weather_observation_modifiers","illumination_observation_modifiers"):
            for raw in dict(env.get(table,{})).values():
                raw=dict(raw)
                for ov in dict(raw.get("sensor_overrides",{})).values():
                    raw.update(dict(ov))
                for k in ("range_factor","awareness_factor","fov_factor"):
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
        sweep_cfg=dict(self.combat_config.get("watch_sweep",{}) or {})
        sweep_on=bool(sweep_cfg.get("enabled",True))
        moving=self.time-float(unit.metadata.get("_moved_at",-1e9))<=float(sweep_cfg.get("moving_window_s",2.0))
        if unit.state in (UnitState.MOVING,UnitState.ATTACKING,UnitState.RETREATING,UnitState.SEARCHING) and (moving or not sweep_on):
            return float(unit.heading_deg)
        # A halt/defend order may explicitly assign a principal observation direction.
        base=None; half=float(sweep_cfg.get("sector_half_width_deg",60.0))
        if unit.current_order:
            params=unit.current_order.params
            if "watch_heading_deg" in params:
                base=float(params["watch_heading_deg"])
            elif "facing_deg" in params:
                base=float(params["facing_deg"])
            if "watch_sweep_half_deg" in params:
                half=float(params["watch_sweep_half_deg"])
        # Halted/defending formations orient toward their assigned objective/sector if available.
        if base is None:
            objective=unit.metadata.get("objective")
            if objective:
                dx=float(objective[0])-unit.pos[0]; dy=float(objective[1])-unit.pos[1]
                if abs(dx)+abs(dy)>1e-9:
                    base=math.degrees(math.atan2(dy,dx))
        if not sweep_on:
            return float(base) if base is not None else float(unit.watch_heading_deg)
        # Halted observers scan: across their assigned sector, or all round when none is
        # assigned (a halted/searching formation does not stare at one bearing forever).
        if base is None or unit.state==UnitState.SEARCHING:
            return float(unit.watch_heading_deg)+float(sweep_cfg.get("all_round_step_deg",90.0))
        if half<=0.0:
            return float(base)
        sign=1.0 if float(unit.metadata.get("_sweep_sign",1.0))>=0 else -1.0
        target=base+sign*half
        if abs(self._angle_delta_deg(target,unit.watch_heading_deg))<=2.0:
            sign=-sign; unit.metadata["_sweep_sign"]=sign; target=base+sign*half
        return target

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
        d=obs.distance_to(tgt)
        # Cheap rejection with the undegraded profile first; environment/terrain modifiers can
        # only shrink it unless an authored factor exceeds 1 (then the bound is infinite).
        self._refresh_sensor_boost_bound()
        boost=float(self._sensor_boost_bound)
        if boost<float("inf"):
            _,f0,fov0,c0,_=self._visual_sensor_base(obs)
            if d>c0*boost:
                if d>f0*boost:
                    return False, f0, 0.0, False
                b0=math.degrees(math.atan2(tgt.pos[1]-obs.pos[1],tgt.pos[0]-obs.pos[0]))
                if abs(self._angle_delta_deg(b0,obs.watch_heading_deg))>min(180.0,fov0*boost*0.5):
                    return False, f0, 0.0, False
        forward,fov,close,_,env_detection=self._visual_sensor_profile(obs,tgt)
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

    DEFAULT_CONFUSION = {"ARMOR":"MECH_INFANTRY","MECH_INFANTRY":"ARMOR","MOTORIZED_INFANTRY":"MECH_INFANTRY",
                         "INFANTRY":"RECON","RECON":"INFANTRY","ARTILLERY":"ARMOR"}

    def _perceived_class(self, prev, tgt: Unit, conf: float) -> str:
        """Classification below IDENTIFIED can be wrong (similar-looking formation types).

        Error probability shrinks with confidence; a classification, once formed, sticks until
        the contact is identified (observers do not re-roll their judgement every glance).
        """
        if prev is not None and prev.state=="CLASSIFIED" and prev.classification not in ("UNKNOWN",""):
            return prev.classification
        cfg=dict(self.combat_config.get("classification_error",{}) or {})
        if not bool(cfg.get("enabled",True)):
            return tgt.branch
        table=dict(self.DEFAULT_CONFUSION); table.update({str(k).upper():str(v).upper() for k,v in dict(cfg.get("confusion",{})).items()})
        wrong=table.get(tgt.branch)
        p=float(cfg.get("max_error_probability",0.25))*max(0.0,min(1.0,(0.82-conf)/0.30))
        if wrong and self.rng.random()<p:
            return wrong
        return tgt.branch

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
        # Observation time actually elapsed since the previous scan: the hazard integrates the
        # past interval, so the first scan (t=0) cannot front-load a whole interval of looking.
        last=getattr(self,"_last_sensor_scan_t",None)
        exposure_dt=0.0 if last is None else max(0.0,min(4.0*sensor_dt,self.time-last))
        self._last_sensor_scan_t=self.time
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
                # The *_per_scan coefficients are calibrated per second of observation.  Convert
                # them to a detection hazard so the chance of acquiring a target in a given time
                # does not depend on how often the sensor model runs (p = 1 - exp(-lambda*dt)).
                edge_p=float(self.combat_config.get("visual_detection_edge_p_per_scan",0.01))
                max_p=float(self.combat_config.get("visual_detection_max_p_per_scan",0.55))
                p1=max(0.001,min(0.98,(edge_p+max_p*range_factor*self._target_signature(tgt))*angular_factor
                                  *self.stress.detection_factor(obs)))
                p=1.0-(1.0-p1)**exposure_dt
                if self.rng.random() > p: continue
                n=(prev.observations+1) if prev else 1
                conf=min(0.98,(prev.confidence if prev else 0.18)+0.16+0.16*range_factor)
                # Location error also grows with range (angular error of the observer's fix).
                err=max(8.0,(1.0-conf)*180.0,d*float(self.combat_config.get("visual_angular_error_rad",0.01)))
                ang=self.rng.random()*math.tau; mag=abs(self.rng.gauss(0,err*0.45))
                est=(tgt.pos[0]+math.cos(ang)*mag,tgt.pos[1]+math.sin(ang)*mag)
                state="DETECTED"; cls="UNKNOWN"
                if n>=2 or conf>=0.52: state="CLASSIFIED"; cls=self._perceived_class(prev,tgt,conf)
                if n>=4 or conf>=0.82: state="IDENTIFIED"; cls=tgt.branch
                tr=Track(track_id=f"{obs.uid}:{tgt.uid}",target_id=tgt.uid,estimated_pos=est,
                         position_error_m=err,classification=cls,confidence=conf,last_seen_time=self.time,
                         observations=n,source="LOCAL",observation_zone=("CLOSE" if in_all_round else "FORWARD"),state=state,
                         belief_confidence=max(prev.belief_confidence if prev else 0.0,conf),
                         existence_confirmed=True,last_confirmed_time=self.time,
                         # Composition is only discerned once the contact is classified; a bare
                         # detection falls back to the (UNKNOWN) classification.
                         perceived_tags=(self.combat.observed_target_tags(tgt) if state!="DETECTED"
                                         else (prev.perceived_tags if prev else None)))
                self.belief.on_observation(tr,cls)
                obs.local_tracks[tgt.uid]=tr
                if prev is None or prev.state in ("STALE","LOST"):
                    self.log("TRACK_UPDATE",observer=obs.uid,target=tgt.uid,state=state,confidence=round(conf,2),source="LOCAL")
                # Local observation is not instantly available to every friendly unit.
                # First, the observer must report it; only then can C2 disseminate the report.
                report_p=1.0-(1.0-float(self.combat_config.get("report_probability_per_observation",0.42)))**max(exposure_dt,1e-6)
                report_key=(obs.uid,tgt.uid)
                min_report_interval=float(self.combat_config.get("observer_report_min_interval_s",12.0))
                can_report=((self.time-self._last_report_sent.get(report_key,-1e9))>=min_report_interval
                            and self.communications.transmitter_operational(obs))
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
            report_source=q.get("report_origin_uid",msg.get("sender_uid"))
            incoming_conf=float(q.get("confidence",.3))*0.92
            observation_time=min(self.time,float(q.get("observation_time",self.time)))
            # Radio and C2 delays can deliver reports out of order. An older report must not
            # replace a newer position or downgrade a locally acquired firing-quality track.
            if old and observation_time < old.last_seen_time:
                self.log("TRACK_REPORT_IGNORED",recipient=recv.uid,target=target,reason="OLDER_OBSERVATION")
                return
            # A better fresh local observation is never overwritten by weaker shared SA.
            local_fresh=bool(old and old.source in ("LOCAL","PROXIMITY")
                             and old.state not in ("STALE","LOST")
                             and self.time-old.last_seen_time<=float(self.combat_config.get("track_stale_s",18.0)))
            preserve_local=bool(local_fresh and old.confidence>=incoming_conf)
            if not preserve_local:
                tr=Track(track_id=f"{recv.uid}:{target}",target_id=target,
                    estimated_pos=tuple(q["estimated_pos"]),position_error_m=float(q.get("position_error_m",100))*1.12,
                    classification=q.get("classification","UNKNOWN"),confidence=incoming_conf,
                    last_seen_time=observation_time,
                    observations=max(1,old.observations if old else 1),source=q.get("track_source","SHARED"),
                    observation_zone="SHARED",state=q.get("state","DETECTED"),belief_confidence=max(old.belief_confidence if old else 0.0,incoming_conf,0.45),
                    existence_confirmed=True,last_confirmed_time=observation_time,
                    perceived_tags=tuple(q.get("perceived_tags") or ()) or None)
                recv.local_tracks[target]=tr
                self.belief.on_observation(tr,q.get("classification","UNKNOWN"),observed_at=observation_time)
            elif observation_time>old.last_confirmed_time:
                # Retain the better local firing solution while accepting the newer report as
                # evidence that the contact still exists.
                self.belief.on_observation(old,observed_at=observation_time)
            # Shared situational awareness may reorient sensors even if local Track was already better.
            self._register_shared_situational_cue(recv,tuple(q["estimated_pos"]),incoming_conf,report_source,q.get("cue_kind","CONTACT"))
            self.log("TRACK_SHARED",source=report_source,recipient=recv.uid,target=target,
                     channel=msg.get("channel"),confidence=round(incoming_conf,2))
            return
        self.log("COMM_RX_UNHANDLED",recipient=recv.uid,message_type=mtype,source=msg.get("sender_uid"))

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
            # The shooter is held to the same rule: firing on a radar fix or a shared report is
            # not the same as watching the target die.
            fresh=str(tr.source).upper() in ("LOCAL","PROXIMITY") and self.time-tr.last_seen_time<=window
            if fresh:
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
