
from __future__ import annotations
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

    DEFAULT_ARMOR_VULNERABILITY = {
        "HEAVY_AT":   {"HEAVY_ARMOR": 1.0,  "MEDIUM_ARMOR": 1.2, "LIGHT_ARMOR": 1.3},
        "LIGHT_AT":   {"HEAVY_ARMOR": 0.35, "MEDIUM_ARMOR": 1.0, "LIGHT_ARMOR": 1.3},
        "AUTOCANNON": {"HEAVY_ARMOR": 0.15, "MEDIUM_ARMOR": 0.9, "LIGHT_ARMOR": 1.4},
    }

    @staticmethod
    def penetration_class(weapon) -> str:
        md=weapon.metadata
        if md.get("penetration_class"):
            return str(md["penetration_class"]).upper()
        if str(weapon.capability).upper()=="ANTI_ARMOR":
            return "HEAVY_AT"
        if str(md.get("inventory_model","")).upper()=="DISPOSABLE_ROUNDS" or float(weapon.range_m)<=400.0:
            return "LIGHT_AT"
        return "AUTOCANNON"

    def armor_vulnerability(self, element:FormationElement, weapon) -> float:
        """Kill-probability multiplier from weapon penetration class x target protection class.

        The weapon's authored kill probabilities describe a typical armored target; an IFV
        cannon must not kill a main battle tank as readily as an APC.
        """
        table={k:dict(v) for k,v in self.DEFAULT_ARMOR_VULNERABILITY.items()}
        for k,v in dict(self.sim.combat_config.get("armor_vulnerability",{}) or {}).items():
            table.setdefault(str(k).upper(),{}).update({str(a).upper():float(b) for a,b in dict(v).items()})
        prot=str(element.metadata.get("protection_class","")).upper()
        return float(table.get(self.penetration_class(weapon),{}).get(prot,1.0))

    def direct_weapon_effect(self, target:Unit, element:FormationElement, weapon) -> Optional[str]:
        """Generic direct anti-equipment hit consequence."""
        md=weapon.metadata
        tags=element.tags
        if "ARMOR" in tags:
            vul=self.armor_vulnerability(element,weapon)
            p_cat=float(md.get("p_catastrophic_on_armor_hit",0.45))*vul
            # Permanent/mission-ending mobility loss is intentionally rare in the generic model.
            # Track/road-wheel hits that are repairable in the field are not represented as a
            # persistent MOBILITY_KILL here; a future maintenance/recovery module can model those
            # temporary impairments explicitly.
            p_mob=float(md.get("p_mobility_kill_on_armor_hit",0.01))*vul
            p_fire=float(md.get("p_firepower_kill_on_armor_hit",0.18))*vul
            p_disabled=float(md.get("p_disabled_on_armor_hit",0.05))*vul
            total=p_cat+p_mob+p_fire+p_disabled
            if total>0.98:
                k=0.98/total; p_cat*=k; p_mob*=k; p_fire*=k; p_disabled*=k
            r=self.sim.rng.random()
            if r<p_cat: return "DESTROYED"
            if r<p_cat+p_mob: return "MOBILITY_KILL"
            if r<p_cat+p_mob+p_fire: return "FIREPOWER_KILL"
            if r<p_cat+p_mob+p_fire+p_disabled: return "DISABLED"
            # A geometrical hit need not produce a persistent vehicle-state kill: armor may
            # defeat the shot or the damage may be locally repairable/non-mission-ending.
            return None
        return "DESTROYED"
