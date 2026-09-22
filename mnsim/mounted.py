from __future__ import annotations
"""Mounted/dismounted transport mechanics.

The module is deliberately simulation/UI neutral.  It treats transport as a capability exposed by
platform metadata (crew/passengers) rather than by vehicle names, so IFV/APC/utility transports can
reuse the same primitive.
"""
from dataclasses import replace
from typing import Iterable, Optional
import copy, math
from .model import Unit, UnitType, UnitState

MOUNTED="MOUNTED"
DISMOUNTED="DISMOUNTED"


def transport_elements(unit: Unit):
    return [e for e in unit.elements.values()
            if e.category.upper()=="EQUIPMENT" and int(e.metadata.get("passengers",0))>0]


def transport_capacity(unit: Unit) -> int:
    return sum(max(0,e.mobile_item_count)*max(0,int(e.metadata.get("passengers",0))) for e in transport_elements(unit))


def required_vehicle_crew(unit: Unit) -> int:
    return sum(max(0,e.mobile_item_count)*max(0,int(e.metadata.get("crew",0))) for e in transport_elements(unit))


def crew_available(unit: Unit) -> int:
    return sum(max(0,e.count) for e in unit.elements.values()
               if e.category.upper()=="PERSONNEL" and (e.role.upper()=="CREW" or "CREW" in e.tags))


def organic_embarked_count(unit: Unit) -> int:
    if str(unit.metadata.get("mount_state",MOUNTED)).upper()!=MOUNTED:
        return 0
    return sum(max(0,e.count) for e in unit.elements.values() if bool(e.metadata.get("dismountable",False)))


def external_embarked_ids(unit: Unit):
    return list(unit.metadata.get("external_embarked_units",[]))


def external_embarked_count(sim, unit: Unit) -> int:
    total=0
    for uid in external_embarked_ids(unit):
        p=sim.units.get(uid)
        if p is not None: total+=max(0,p.personnel)
    return total


def free_seats(sim, unit: Unit) -> int:
    return max(0, transport_capacity(unit)-organic_embarked_count(unit)-external_embarked_count(sim,unit))


def carrier_operable(unit: Unit) -> bool:
    req=required_vehicle_crew(unit)
    return req<=0 or crew_available(unit)>=req


def initialize_transport_metadata(unit: Unit):
    if not transport_elements(unit): return
    unit.metadata.setdefault("mount_state",str(unit.unit_type.metadata.get("mount_state_default",MOUNTED)).upper())
    unit.metadata.setdefault("external_embarked_units",[])
    unit.metadata.setdefault("embark_radius_m",20.0)
    unit.metadata.setdefault("embark_time_s",20.0)
    unit.metadata.setdefault("dismount_time_s",15.0)


def _unique_child_id(sim, parent: Unit) -> str:
    base=f"{parent.uid}-DMT"; uid=base; n=2
    while uid in sim.units:
        uid=f"{base}-{n}"; n+=1
    return uid


def dismount_organic(sim, parent: Unit) -> Optional[Unit]:
    """Detach organic dismountable personnel into one child tactical formation.

    Elements are physically transferred, not copied, preserving strength and weapon/ammo state.
    """
    initialize_transport_metadata(parent)
    if str(parent.metadata.get("mount_state",MOUNTED)).upper()==DISMOUNTED:
        cid=parent.metadata.get("dismount_child_id")
        return sim.units.get(cid) if cid else None
    moved={eid:e for eid,e in list(parent.elements.items()) if e.category.upper()=="PERSONNEL" and bool(e.metadata.get("dismountable",False)) and e.count>0}
    if not moved:
        parent.metadata["mount_state"]=DISMOUNTED
        return None
    for eid in moved: parent.elements.pop(eid,None)
    uid=_unique_child_id(sim,parent)
    typ=UnitType(name=f"{parent.unit_type.name}_DISMOUNT",branch="INFANTRY",
                 max_speed_mps=float(parent.metadata.get("dismount_speed_mps",1.8)),
                 detection_range_m=parent.unit_type.detection_range_m,elements=[],
                 metadata={"mobility_class":"FOOT","formation_family":"INF","echelon":parent.echelon})
    child=Unit(uid=uid,name=f"{parent.name} dismounts",side=parent.side,echelon=parent.echelon,
               unit_type=typ,pos=tuple(parent.pos),heading_deg=parent.heading_deg,
               watch_heading_deg=parent.watch_heading_deg,parent_id=parent.uid,
               metadata={"dismounted_from":parent.uid,"organic_dismount":True},elements=moved)
    sim.add_unit(child); parent.children.append(uid)
    parent.metadata["dismount_child_id"]=uid; parent.metadata["mount_state"]=DISMOUNTED
    sim.log("DISMOUNT_COMPLETE",unit=parent.uid,dismount=uid,personnel=child.personnel)
    return child


def mount_organic(sim, parent: Unit) -> bool:
    cid=parent.metadata.get("dismount_child_id"); child=sim.units.get(cid) if cid else None
    if child is None or not child.alive or not parent.alive or not carrier_operable(parent):
        return False
    if free_seats(sim, parent) < child.personnel:
        return False
    radius=float(parent.metadata.get("embark_radius_m",20.0))
    if math.dist(parent.pos,child.pos)>radius: return False
    for eid,e in list(child.elements.items()):
        if eid in parent.elements:
            dst=parent.elements[eid]; dst.count+=e.count; dst.initial_count+=e.initial_count
        else: parent.elements[eid]=e
        child.elements.pop(eid,None)
    child.active=False; child.state=UnitState.AGGREGATED
    child.metadata["embarked_in"]=parent.uid
    parent.metadata["mount_state"]=MOUNTED
    sim.log("MOUNT_COMPLETE",unit=parent.uid,dismount=child.uid,personnel=parent.personnel)
    return True


def _allocate_vehicle_seats(sim, carrier: Unit, passenger_count: int):
    """Greedy stable allocation over individual vehicle slots inside an aggregate formation."""
    slots=[]
    organic=max(0,organic_embarked_count(carrier))
    existing=dict(carrier.metadata.get("external_passenger_allocations",{}))
    occupied={}
    for allocs in existing.values():
        for a in allocs:
            key=(str(a.get("element")),int(a.get("vehicle_index",0))); occupied[key]=occupied.get(key,0)+int(a.get("seats",0))
    # Organic dismounts consume seats first; exact real-world vehicle assignment is abstracted.
    for e in transport_elements(carrier):
        cap=max(0,int(e.metadata.get("passengers",0)))
        e.ensure_item_states()
        for i, state in enumerate(e.item_states):
            if state not in ("OPERATIONAL", "FIREPOWER_KILL"):
                continue
            use=min(cap,organic); organic-=use
            occupied[(e.eid,i)]=occupied.get((e.eid,i),0)+use
            slots.append((e.eid,i,cap))
    remaining=passenger_count; alloc=[]
    for eid,i,cap in slots:
        avail=max(0,cap-occupied.get((eid,i),0))
        take=min(avail,remaining)
        if take>0: alloc.append({"element":eid,"vehicle_index":i,"seats":take}); remaining-=take
        if remaining<=0: break
    return alloc if remaining<=0 else None

def board_external(sim, passenger: Unit, carrier: Unit) -> bool:
    initialize_transport_metadata(carrier)
    if not passenger.alive or not carrier.alive or not carrier_operable(carrier): return False
    if passenger.side!=carrier.side or passenger.uid==carrier.uid: return False
    if passenger.metadata.get("embarked_in"): return False
    need=max(1,passenger.personnel)
    if free_seats(sim,carrier)<need: return False
    allocation=_allocate_vehicle_seats(sim,carrier,need)
    if allocation is None:return False
    radius=float(carrier.metadata.get("embark_radius_m",20.0))
    if math.dist(passenger.pos,carrier.pos)>radius: return False
    carrier.metadata.setdefault("external_embarked_units",[]).append(passenger.uid)
    carrier.metadata.setdefault("external_passenger_allocations",{})[passenger.uid]=allocation
    passenger.metadata["embarked_in"]=carrier.uid
    passenger.metadata["vehicle_seat_allocation"]=copy.deepcopy(allocation)
    passenger.metadata["embarked_at_t"]=sim.time
    passenger.pos=tuple(carrier.pos); passenger.active=False; passenger.state=UnitState.AGGREGATED
    sim.log("BOARD_COMPLETE",unit=passenger.uid,carrier=carrier.uid,personnel=passenger.personnel)
    return True


def disembark_external(sim, carrier: Unit, passenger_uid: str|None=None) -> list[Unit]:
    ids=list(carrier.metadata.get("external_embarked_units",[]))
    if passenger_uid and str(passenger_uid).upper()!="ALL": ids=[x for x in ids if x==passenger_uid]
    out=[]
    for uid in ids:
        p=sim.units.get(uid)
        if p is None: continue
        p.active=True
        p.state=UnitState.IDLE if p.current_strength > 0 else UnitState.DESTROYED
        p.pos=tuple(carrier.pos)
        p.metadata.pop("embarked_in",None); p.metadata.pop("vehicle_seat_allocation",None)
        carrier.metadata.get("external_passenger_allocations",{}).pop(uid,None)
        if uid in carrier.metadata.get("external_embarked_units",[]): carrier.metadata["external_embarked_units"].remove(uid)
        sim.log("DISEMBARK_COMPLETE",unit=uid,carrier=carrier.uid,personnel=p.personnel); out.append(p)
    return out


def handle_transport_damage(sim, carrier: Unit, element, item_index: int, detached=None):
    """Reconcile passenger ownership after a vehicle is removed or immobilized.

    A group wholly on a detached vehicle follows that vehicle. A group spread across
    vehicles disembarks intact. Organic infantry also dismount as one formation,
    matching the existing organic mount/dismount abstraction. This transfer does not
    add casualties; it releases the survivors already represented by personnel counts.
    """
    if int(element.metadata.get("passengers", 0)) <= 0:
        return
    if organic_embarked_count(carrier):
        child = dismount_organic(sim, carrier)
        if child is not None:
            sim.log("EMERGENCY_DISMOUNT", unit=carrier.uid, child=child.uid,
                    element=element.eid, item_index=item_index)
    allocations = carrier.metadata.setdefault("external_passenger_allocations", {})
    for uid, seats in list(allocations.items()):
        affected = [a for a in seats if a["element"] == element.eid
                    and a["vehicle_index"] == item_index]
        if not affected:
            continue
        passenger = sim.units.get(uid)
        if detached is not None and len(affected) == len(seats) and passenger is not None:
            initialize_transport_metadata(detached)
            transferred = [dict(a, vehicle_index=0) for a in affected]
            detached.metadata["external_embarked_units"].append(uid)
            detached.metadata.setdefault("external_passenger_allocations", {})[uid] = transferred
            carrier.metadata["external_embarked_units"].remove(uid)
            allocations.pop(uid)
            passenger.metadata["embarked_in"] = detached.uid
            passenger.metadata["vehicle_seat_allocation"] = copy.deepcopy(transferred)
            passenger.pos = tuple(detached.pos)
            sim.log("PASSENGER_TRANSFER", unit=uid, source=carrier.uid, carrier=detached.uid,
                    element=element.eid, source_item_index=item_index, allocation=transferred)
        else:
            disembark_external(sim, carrier, uid)
    # A destroyed slot stays in item_states; only physical detachment shifts indices.
    if detached is not None:
        for uid, seats in allocations.items():
            for a in seats:
                if a["element"] == element.eid and a["vehicle_index"] > item_index:
                    a["vehicle_index"] -= 1
            passenger = sim.units.get(uid)
            if passenger is not None:
                passenger.metadata["vehicle_seat_allocation"] = copy.deepcopy(seats)
    if carrier.current_strength <= 0:
        carrier.state = UnitState.DESTROYED
