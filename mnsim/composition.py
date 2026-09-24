"""Formation composition subsystem of :class:`mnsim.simulation.Simulation` (mixin).

Aggregation/deaggregation of formations, detachment of immobilised vehicles and lookup of
physical equipment items by stable id.  Split out of simulation.py without changing behaviour.
"""
from __future__ import annotations

import copy
from typing import List

from .model import FormationElement, Unit, UnitState, UnitType


class CompositionMixin:
    def aggregate_units(self,new_uid:str,name:str,child_ids:List[str],echelon="COY",pos=None):
        if new_uid in self.units:
            raise ValueError("Aggregate unit ID already exists")
        child_ids = list(dict.fromkeys(child_ids))
        unavailable=[uid for uid in child_ids if uid not in self.units or not self.units[uid].active]
        if unavailable:
            raise ValueError(f"Unknown or inactive aggregate children: {unavailable}")
        children=[self.units[x] for x in child_ids]
        if not children: raise ValueError("No children to aggregate")
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
        for c in children:
            # In-flight damage follows the physical elements into the aggregate.
            self.events.remap_formation_damage(c.uid,new_uid,{eid:f"{c.uid}:{eid}" for eid in c.elements})
            c.active=False; c.state=UnitState.AGGREGATED; c.parent_id=new_uid
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
            self.events.remap_formation_damage(p.uid,cid,ids)
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
            lead = not restored
            offset = dict(p.metadata.get("child_offsets", {})).get(cid, (0.0, 0.0))
            c.current_order = self._split_order_for_child(p.current_order, cid, offset, lead)
            c.order_queue = [o for o in (self._split_order_for_child(q, cid, offset, lead)
                                         for q in p.order_queue) if o is not None]
            c.local_tracks = self._merge_tracks([c.local_tracks, p.local_tracks])
            c.target_id = None
            self._clear_navigation_state(c)
            for key in ("search_arrived_t","target_acquired_t","_direct_fire_state",
                        "_local_direct_target_locks","_direct_fire_cycle_state"):
                c.metadata.pop(key, None)
            # Keep the aggregate's order clock so HOLD durations/conditions do not restart.
            if "order_started_t" in p.metadata and c.current_order is not None:
                c.metadata["order_started_t"] = p.metadata["order_started_t"]
            else:
                c.metadata.pop("order_started_t", None)
            restored.append(cid)
        p.active = False
        p.state = UnitState.AGGREGATED
        p.metadata["deaggregated"] = True
        self.log("DEAGGREGATE", parent=parent_uid, children=restored)
        return restored

    #: Orders that describe one shared physical action; only the lead subordinate inherits them.
    _INDIVISIBLE_ORDERS = frozenset({"BUILD_BARRICADE", "BOARD", "DISEMBARK", "MOUNT", "DISMOUNT",
                                     "STRIKE_INFRASTRUCTURE", "ENTER_BUILDING", "EXIT_BUILDING"})

    def _split_order_for_child(self, order, child_uid, offset, lead):
        """Per-subordinate copy of an aggregate order: unique id, destination shifted by the
        subordinate's formation offset; indivisible actions go to the lead subordinate only."""
        if order is None:
            return None
        if str(order.kind).upper() in self._INDIVISIBLE_ORDERS and not lead:
            return None
        out = copy.deepcopy(order)
        out.order_id = f"{order.order_id}@{child_uid}"
        dx, dy = float(offset[0]), float(offset[1])
        for key in ("destination", "center", "search_reference"):
            v = out.params.get(key)
            if isinstance(v, (list, tuple)) and len(v) >= 2 and str(order.kind).upper() not in self._INDIVISIBLE_ORDERS:
                out.params[key] = [float(v[0]) + dx, float(v[1]) + dy]
        return out

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
        self.events.remap_equipment_effects(unit.uid,element.eid,item_index,child.uid)
        unit.children.append(new_uid)
        unit.metadata.setdefault("detached_items",{})[new_uid]={
            "element":element.eid,"state":state,"detached_at":self.time,
            "position":tuple(unit.pos)
        }
        self.log("DEAGGREGATE_DAMAGED_ITEM",parent=unit.uid,child=new_uid,element=element.eid,
                 source_item_index=item_index,state=state,parent_remaining=element.count)

        # Observers that were tracking the formation still see the stranded vehicle: give them a
        # track on the new vehicle entity (same estimate), so a split is not mistaken for a kill
        # and the vehicle does not vanish from their picture.
        for obs in self.units.values():
            if obs.side==unit.side:
                continue
            tr=obs.local_tracks.get(unit.uid)
            if tr is None or tr.state in ("LOST","DESTROYED") or new_uid in obs.local_tracks:
                continue
            ct=copy.deepcopy(tr)
            ct.track_id=f"{obs.uid}:{new_uid}"; ct.target_id=new_uid
            obs.local_tracks[new_uid]=ct

        # If every physical item has been transferred out, there is no residual aggregate entity.
        if unit.current_strength<=0:
            unit.active=False
            unit.metadata["depleted_by_detachment"]=True
            self.log("FORMATION_DEPLETED_BY_DETACHMENT",unit=unit.uid,children=list(unit.children))
        return child

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
