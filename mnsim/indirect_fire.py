
from __future__ import annotations
import math
from typing import Iterable, Tuple
from .model import Unit, UnitState, FormationElement, WeaponModel
from .formation_geometry import footprint_for, sample_person_position, equipment_item_position, normalized_ellipse_radius


class IndirectFireResolver:
    """Area-effect indirect-fire model.

    The firing solution is based on the shooter's FoW Track, not target ground truth.
    Each round receives its own impact point.  Dispersion combines:
      1) weapon/system dispersion represented by a configurable circular CEP, and
      2) target-location uncertainty from the track.

    The resulting impact point is then resolved spatially against nearby formation
    elements. This deliberately avoids the old "artillery directly rolls against one
    selected element" mechanic.

    Parameters in demo.json are generic M&S tuning values and are not a calibrated
    representation of a particular artillery system.
    """

    def __init__(self, sim):
        self.sim = sim

    @staticmethod
    def _cep_to_sigma(cep_m: float) -> float:
        # For an isotropic bivariate normal, P(R <= CEP)=0.5.
        return max(0.1, cep_m / math.sqrt(2.0 * math.log(2.0)))

    def _impact_point(self, aim: Tuple[float, float], weapon_cep_m: float,
                      track_error_m: float) -> Tuple[float, float, float]:
        # Treat the track's "position_error_m" as a conservative radial uncertainty
        # scale and combine independent error sources in quadrature.
        sigma_weapon = self._cep_to_sigma(max(1.0, weapon_cep_m))
        sigma_track = max(0.0, track_error_m) * 0.55
        sigma = math.sqrt(sigma_weapon * sigma_weapon + sigma_track * sigma_track)
        dx = self.sim.rng.gauss(0.0, sigma)
        dy = self.sim.rng.gauss(0.0, sigma)
        return aim[0] + dx, aim[1] + dy, sigma

    @staticmethod
    def _radius_for_element(element: FormationElement, weapon: WeaponModel) -> float:
        md = weapon.metadata
        tags = element.tags
        if "ARMOR" in tags:
            return float(md.get("effect_radius_armor_m", 14.0))
        if element.category.upper() == "EQUIPMENT":
            return float(md.get("effect_radius_equipment_m", 28.0))
        return float(md.get("effect_radius_personnel_m", 48.0))

    @staticmethod
    def _base_effect_probability(element: FormationElement, weapon: WeaponModel) -> float:
        md = weapon.metadata
        tags = element.tags
        if "ARMOR" in tags:
            return float(md.get("effect_p_armor", 0.18))
        if element.category.upper() == "EQUIPMENT":
            return float(md.get("effect_p_equipment", 0.42))
        return float(md.get("effect_p_personnel", 0.62))

    def _personnel_losses(self, target: Unit, element: FormationElement, impact: Tuple[float,float],
                          weapon: WeaponModel, footprint, max_loss_override: int | None = None) -> tuple[int,int]:
        """Resolve one shell against a personnel element distributed inside the formation footprint.

        Personnel are not persistent individual entities. For each impact we Monte-Carlo sample their
        likely locations from the formation density and then apply the shell's radial effect. This
        preserves a cheap aggregate representation while making dispersion materially reduce exposure.
        """
        radius=self._radius_for_element(element,weapon)
        if radius<=0 or element.count<=0:return 0,0
        base=self._base_effect_probability(element,weapon)
        protection=1.0
        if target.state==UnitState.DEFENDING:
            protection*=float(weapon.metadata.get("defending_protection_factor",0.68))
        posture=str(target.metadata.get("dispersion_posture","NORMAL")).upper()
        posture_protect=self.sim.combat_config.get("formation_posture_modifiers",{}).get(posture,{})
        protection*=float(posture_protect.get("indirect_casualty_factor",1.0))
        casualties=0; exposed=0
        max_loss=int(weapon.metadata.get("max_personnel_loss_per_round",6))
        if max_loss_override is not None:
            max_loss=max(0,min(max_loss,int(max_loss_override)))
        if max_loss<=0:return 0,0
        for _ in range(element.count):
            px,py=sample_person_position(self.sim,target,footprint)
            d=math.dist((px,py),impact)
            if d>radius: continue
            exposed+=1
            proximity=max(0.0,1.0-(d/radius)**1.45)
            p=base*(0.12+0.88*proximity)*protection
            if self.sim.rng.random()<max(0.0,min(0.97,p)):
                casualties+=1
                if casualties>=max_loss:break
        return casualties,exposed


    def fire_mission(self, shooter: Unit, target: Unit, source_element: FormationElement,
                     weapon: WeaponModel, mode: str = "FIRE_SUPPORT") -> None:
        """Request a mission. Actual firing occurs later through FireControlEngine."""
        self.sim.fire_control.request(shooter,target,source_element,weapon,mode)


    def _fire_pattern(self, shooter: Unit):
        name=str(shooter.metadata.get("artillery_fire_profile","CONCENTRATED")).upper()
        profile=dict(self.sim.artillery_doctrine_profiles.get(name,{}))
        if not profile:
            profile={"pattern":"RADIAL","pattern_radius_m":0.0,"rounds_per_piece":1}
        return name,profile

    @staticmethod
    def _pattern_offset(profile, idx:int, rounds:int):
        """Deliberate aim-point offset before random weapon/track dispersion is applied."""
        pattern=str(profile.get("pattern","RADIAL")).upper()
        if pattern=="LINE":
            spacing=float(profile.get("pattern_spacing_m",40.0))
            center=(rounds-1)/2.0
            return ((idx-center)*spacing,0.0)
        radius=float(profile.get("pattern_radius_m",0.0))
        if radius<=0 or rounds<=1:
            return (0.0,0.0)
        angle=2.0*math.pi*(idx/rounds)
        return (math.cos(angle)*radius,math.sin(angle)*radius)

    def launch_prepared_mission(self, shooter: Unit, source_element: FormationElement,
                                weapon: WeaponModel, target_id: str, aim: Tuple[float,float],
                                track_error_m: float, track_confidence: float,
                                observation_time: float, mode: str, request_time: float):
        perceived_d=math.dist(shooter.pos,aim)
        if perceived_d > weapon.range_m:
            self.sim.log("FIRE_MISSION_ABORTED",shooter=shooter.uid,target=target_id,weapon=weapon.name,
                         reason="AIM_POINT_OUT_OF_RANGE")
            return

        power=shooter.firepower(source_element,weapon)
        pieces=power.participants
        if pieces<=0:
            self.sim.log("FIRE_MISSION_ABORTED",shooter=shooter.uid,weapon=weapon.name,
                         reason="WEAPON_OR_CREW_UNAVAILABLE")
            return
        profile_name,profile=self._fire_pattern(shooter)
        per_piece=max(1,int(profile.get("rounds_per_piece",weapon.metadata.get("salvo_rounds_per_piece",1))))
        requested_rounds=pieces*per_piece
        if weapon.ammo_remaining==0:
            return
        if weapon.ammo_remaining>0:
            rounds=min(requested_rounds,weapon.ammo_remaining); weapon.ammo_remaining-=rounds
        else:
            rounds=requested_rounds
        if rounds<=0:
            return

        shooter.weapon_last_fire[f"{source_element.eid}:{weapon.name}"]=self.sim.time
        shooter.target_id=target_id
        self.sim.notify_indirect_fire_launch(shooter,weapon.name,mode,projectile_count=rounds)
        if weapon.ammo_remaining==0:
            self.sim.log("AMMO_DEPLETED",unit=shooter.uid,source_element=source_element.eid,weapon=weapon.name)

        cep=float(weapon.metadata.get("dispersion_cep_m",55.0))
        tof=self.sim.rng.uniform(
            float(self.sim.combat_config.get("artillery_time_of_flight_min_s",8.0)),
            float(self.sim.combat_config.get("artillery_time_of_flight_max_s",15.0))
        )
        track_age_at_fire=max(0.0,self.sim.time-observation_time)

        self.sim.log(
            "INDIRECT_FIRE",shooter=shooter.uid,target=target_id,source_element=source_element.eid,
            weapon=weapon.name,mode=mode,rounds=rounds,
            aim=[round(aim[0],1),round(aim[1],1)],track_error_m=round(track_error_m,1),
            track_confidence=round(track_confidence,2),track_age_at_fire_s=round(track_age_at_fire,1),
            dispersion_cep_m=round(cep,1),time_of_flight_s=round(tof,1),
            request_to_fire_s=round(self.sim.time-request_time,1),ammo_remaining=weapon.ammo_remaining,
            fire_profile=profile_name,
        )

        for round_idx in range(rounds):
            ox,oy=self._pattern_offset(profile,round_idx,rounds)
            deliberate_aim=(aim[0]+ox,aim[1]+oy)
            ix,iy,sigma=self._impact_point(deliberate_aim,cep,track_error_m)
            self.sim.events.push(
                self.sim.time+tof,"ARTY_IMPACT_RESOLVE",
                shooter=shooter.uid,target=target_id,weapon=weapon.name,round=round_idx+1,
                pos=(ix,iy),mode=mode,fire_profile=profile_name,
                deliberate_aim=deliberate_aim,
            )


    def _equipment_blast_effect(self, element:FormationElement, distance_m:float, weapon:WeaponModel):
        """Return (effect, geometry_label) for one equipment item near an artillery impact.

        Area-fire vulnerability is intentionally separated from geometry.  The weapon defines
        blast/fragment bands; the target element's ``protection_class`` selects a configurable
        vulnerability profile.  This keeps counterfire survivability data-driven and prevents
        every unarmored system (gun, radar, truck, etc.) from sharing one overly lethal rule.
        """
        md=weapon.metadata
        tags=element.tags
        armored=("ARMOR" in tags)
        protection=str(element.metadata.get("protection_class", "DEFAULT_SOFT")).upper()
        profiles=dict(self.sim.combat_config.get("indirect_fire_vulnerability_profiles", {}))
        soft_profile=dict(profiles.get("DEFAULT_SOFT", {}))
        soft_profile.update(dict(profiles.get(protection, {})))
        # Generic geometry bands. These remain weapon/munition driven.
        direct_r=float(md.get("direct_hit_radius_m",4.0))
        near_r=float(md.get("near_hit_radius_armor_m",18.0) if armored else md.get("near_hit_radius_equipment_m",32.0))
        frag_r=float(md.get("fragment_effect_radius_armor_m",45.0) if armored else md.get("fragment_effect_radius_equipment_m",70.0))
        if distance_m<=direct_r:
            geom="DIRECT"
            if armored:
                p_destroy=float(md.get("p_destroy_direct_armor",0.82))
                if self.sim.rng.random()<p_destroy:return "DESTROYED",geom
                # A non-catastrophic direct/very-close hit may still mission-kill an armored system.
                return "FIREPOWER_KILL",geom
            p_destroy=float(md.get("p_destroy_direct_soft",soft_profile.get("p_destroy_direct",0.95)))
            if self.sim.rng.random()<p_destroy:return "DESTROYED",geom
            p_disable=float(soft_profile.get("p_disable_direct_survivor",0.75))
            return ("DISABLED" if self.sim.rng.random()<p_disable else None),geom
        if distance_m<=near_r:
            geom="NEAR"
            r=self.sim.rng.random()
            if armored:
                p_cat=float(md.get("p_destroy_near_armor",0.08))
                p_mob=float(md.get("p_mobility_near_armor",0.12))
                p_fire=float(md.get("p_firepower_near_armor",0.18))
                if r<p_cat:return "DESTROYED",geom
                if r<p_cat+p_mob:return "MOBILITY_KILL",geom
                if r<p_cat+p_mob+p_fire:return "FIREPOWER_KILL",geom
                return None,geom
            p_disable=float(md.get("p_disable_near_soft",soft_profile.get("p_disable_near",0.62)))
            return ("DISABLED" if r<p_disable else None),geom
        if distance_m<=frag_r:
            geom="FRAGMENT"
            r=self.sim.rng.random()
            if armored:
                p_mob=float(md.get("p_mobility_fragment_armor",0.03))
                p_fire=float(md.get("p_firepower_fragment_armor",0.08))
                if r<p_mob:return "MOBILITY_KILL",geom
                if r<p_mob+p_fire:return "FIREPOWER_KILL",geom
                return None,geom
            p_disable=float(md.get("p_disable_fragment_soft",soft_profile.get("p_disable_fragment",0.24)))
            return ("DISABLED" if r<p_disable else None),geom
        return None,"OUTSIDE"

    def resolve_impact(self,payload):
        shooter=self.sim.units.get(payload.get("shooter"))
        if not shooter:return
        weapon=None
        for e in shooter.elements.values():
            for w in e.weapons:
                if w.name==payload.get("weapon"):
                    weapon=w; break
            if weapon:break
        if weapon is None:return

        impact=tuple(payload["pos"]); ix,iy=impact
        friendly_fire=bool(self.sim.combat_config.get("indirect_friendly_fire",True))
        candidates=[u for u in self.sim.units.values() if u.alive and u.uid!=shooter.uid]
        if not friendly_fire:candidates=[u for u in candidates if u.side!=shooter.side]

        effects=0; exposed_personnel=0
        geometry_counts={"DIRECT":0,"NEAR":0,"FRAGMENT":0}
        footprint_hits=[]
        bridge_effects=[]
        building_effects=[]
        if getattr(self.sim,"terrain",None):
            bridge_effects=self.sim.terrain.apply_bridge_impact(impact,weapon.metadata,self.sim.rng)
            for rec in bridge_effects:
                self.sim.log("BRIDGE_DAMAGE",shooter=shooter.uid,bridge=rec["bridge"],weapon=weapon.name,
                             damage=round(rec["damage"],1),integrity=round(rec["integrity"],1),
                             max_integrity=round(rec["max_integrity"],1),geometry=rec["geometry"])
                if rec["destroyed"]:
                    self.sim.log("BRIDGE_DESTROYED",shooter=shooter.uid,bridge=rec["bridge"],weapon=weapon.name,pos=[round(ix,1),round(iy,1)])
            building_effects=self.sim.terrain.apply_building_impact(impact,weapon.metadata,self.sim.rng)
            for rec in building_effects:
                self.sim.log("BUILDING_DAMAGE",shooter=shooter.uid,building=rec["building"],weapon=weapon.name,damage=round(rec["damage"],1),integrity=round(rec["integrity"],1))
                if rec["destroyed"]:
                    bld=next((a for a in self.sim.terrain.areas if str(a.get("id"))==rec["building"]),None)
                    self.sim.log("BUILDING_DESTROYED",shooter=shooter.uid,building=rec["building"],weapon=weapon.name)
                    self.sim._handle_destroyed_building(bld,shooter.uid,weapon.name)
        for victim in candidates:
            fp=footprint_for(self.sim,victim)
            # Cheap broad-phase check that includes formations whose edge, rather than centroid, is hit.
            max_effect=float(weapon.metadata.get("effect_radius_personnel_m",48.0))
            max_effect=max(max_effect,float(weapon.metadata.get("fragment_effect_radius_equipment_m",70.0)))
            vd=math.dist(victim.pos,impact)
            if vd>fp.semi_major+max_effect+20.0:continue
            nr=normalized_ellipse_radius(fp,impact,victim.pos)
            awareness_r=float(weapon.metadata.get("incoming_fire_awareness_radius_m",110.0))
            if nr<=1.0 or vd<=awareness_r+fp.semi_major:
                self.sim._register_threat_cue(victim,shooter.uid,"INDIRECT_FIRE")
            victim_effects=0
            # IMPORTANT: max_personnel_loss_per_round is a shell-level casualty cap for this
            # formation, not a separate allowance for every squad/HQ/support element.  Applying
            # it per FormationElement made large aggregate echelons catastrophically more
            # vulnerable simply because they contain more bookkeeping elements.
            personnel_loss_cap=max(0,int(weapon.metadata.get("max_personnel_loss_per_round",6)))
            personnel_losses_this_round=0
            for el in victim.elements.values():
                if not el.alive:continue
                if not victim.element_exposed(el):continue
                if el.category.upper()=="EQUIPMENT":
                    el.ensure_item_states()
                    for item_i,state in enumerate(list(el.item_states)):
                        if state=="DESTROYED":continue
                        item_pos=equipment_item_position(victim,el,item_i,fp)
                        item_d=math.dist(item_pos,impact)
                        effect,geom=self._equipment_blast_effect(el,item_d,weapon)
                        if effect:
                            effects+=1; victim_effects+=1
                            geometry_counts[geom]=geometry_counts.get(geom,0)+1
                            self.sim.events.push(
                                self.sim.time+0.15,"EQUIPMENT_EFFECT",target=victim.uid,source=shooter.uid,
                                element=el.eid,item_index=item_i,item_id=el.item_id_at(item_i),
                                effect=effect,weapon=weapon.name,reason=f"ARTILLERY_{geom}"
                            )
                else:
                    remaining=max(0,personnel_loss_cap-personnel_losses_this_round)
                    loss,exposed=self._personnel_losses(victim,el,impact,weapon,fp,remaining)
                    exposed_personnel+=exposed
                    personnel_losses_this_round+=loss
                    if loss>0:
                        effects+=loss; victim_effects+=loss
                        self.sim.events.push(
                            self.sim.time+0.15,"ELEMENT_LOSS",target=victim.uid,source=shooter.uid,
                            element=el.eid,count=loss,weapon=weapon.name,cue_type="INDIRECT_FIRE"
                        )
            if victim_effects or nr<=1.0:
                footprint_hits.append({"unit":victim.uid,"length_m":round(fp.length_m,1),
                    "width_m":round(fp.width_m,1),"posture":str(victim.metadata.get("dispersion_posture","NORMAL")),
                    "impact_inside":nr<=1.0,"effects":victim_effects})
        self.sim.log("ARTY_IMPACT",shooter=shooter.uid,target=payload.get("target"),weapon=weapon.name,
                     round=payload.get("round"),pos=[round(ix,1),round(iy,1)],effects=effects,
                     exposed_personnel=exposed_personnel,geometry_counts=geometry_counts,
                     footprint_hits=footprint_hits,bridge_effects=bridge_effects,building_effects=building_effects,mode=payload.get("mode"))
