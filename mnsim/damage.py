
from __future__ import annotations
import math
from typing import Optional
from .model import FormationElement, Unit


class DamageResolver:
    """Component/state damage for equipment.

    Equipment is no longer only "count alive/dead". Armored vehicles and artillery pieces can
    be operational, mobility-killed, firepower-killed, disabled, or destroyed.

    The model intentionally exposes probabilities through element/weapon metadata. Demo values
    are generic M&S tuning assumptions, not calibrated vulnerability data for a named platform.
    """

    STATES=("OPERATIONAL","MOBILITY_KILL","FIREPOWER_KILL","DISABLED","DESTROYED")

    def __init__(self,sim):
        self.sim=sim

    def apply_equipment_effect(self, unit:Unit, element:FormationElement, effect:str,
                               source:str="", weapon:str="", reason:str="", item_index:int|None=None):
        element.ensure_item_states()
        candidates=[i for i,s in enumerate(element.item_states) if s!="DESTROYED"]
        if not candidates:
            return None
        # Spatial/area-effect callers may identify the actually exposed item. Direct-fire callers
        # may omit it, in which case the existing stochastic selection is retained.
        if item_index is not None:
            # A spatially identified item can disappear before the delayed effect arrives.
            # Never redirect that hit onto a different vehicle in the formation.
            if item_index not in candidates:
                return None
            idx=item_index
        else:
            operational=[i for i in candidates if element.item_states[i]=="OPERATIONAL"]
            idx=self.sim.rng.choice(operational or candidates)
        old=element.item_states[idx]
        new=effect
        # Escalation rules for a second meaningful hit.
        if old=="MOBILITY_KILL" and effect in ("MOBILITY_KILL","FIREPOWER_KILL","DISABLED"):
            new="DISABLED"
        elif old=="FIREPOWER_KILL" and effect in ("MOBILITY_KILL","FIREPOWER_KILL","DISABLED"):
            new="DISABLED"
        elif old=="DISABLED" and effect!="OPERATIONAL":
            new="DESTROYED"
        element.item_states[idx]=new
        element.sync_count_from_states()
        self.sim.log("EQUIPMENT_STATE_CHANGE",unit=unit.uid,element=element.eid,item_index=idx,
                     old_state=old,new_state=new,source=source,weapon=weapon,reason=reason)
        if new == "DESTROYED":
            from .crew import evacuate_destroyed_vehicle_crew
            evacuate_destroyed_vehicle_crew(self.sim, unit, element, idx, source=source)
        detached = None
        if new in ("MOBILITY_KILL","FIREPOWER_KILL","DISABLED","DESTROYED"):
            detached = self.sim._maybe_split_damaged_equipment(unit,element,idx,new)
        if new in ("MOBILITY_KILL", "DISABLED", "DESTROYED"):
            from .mounted import handle_transport_damage
            handle_transport_damage(self.sim, unit, element, idx, detached)
        return idx

    def direct_weapon_effect(self, target:Unit, element:FormationElement, weapon) -> Optional[str]:
        """Generic direct anti-equipment hit consequence."""
        md=weapon.metadata
        tags=element.tags
        if "ARMOR" in tags:
            p_cat=float(md.get("p_catastrophic_on_armor_hit",0.45))
            # Permanent/mission-ending mobility loss is intentionally rare in the generic model.
            # Track/road-wheel hits that are repairable in the field are not represented as a
            # persistent MOBILITY_KILL here; a future maintenance/recovery module can model those
            # temporary impairments explicitly.
            p_mob=float(md.get("p_mobility_kill_on_armor_hit",0.01))
            p_fire=float(md.get("p_firepower_kill_on_armor_hit",0.18))
            p_disabled=float(md.get("p_disabled_on_armor_hit",0.05))
            r=self.sim.rng.random()
            if r<p_cat: return "DESTROYED"
            if r<p_cat+p_mob: return "MOBILITY_KILL"
            if r<p_cat+p_mob+p_fire: return "FIREPOWER_KILL"
            if r<p_cat+p_mob+p_fire+p_disabled: return "DISABLED"
            # A geometrical hit need not produce a persistent vehicle-state kill: armor may
            # defeat the shot or the damage may be locally repairable/non-mission-ending.
            return None
        return "DESTROYED"
