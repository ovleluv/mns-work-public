"""Transfer explicitly modelled survivors out of destroyed armored vehicles."""
import copy

from .firepower import _crew_spec
from .model import Order, Unit, UnitState, UnitType
from .ownership import transfer_personnel


def evacuate_destroyed_vehicle_crew(sim, unit, element, item_index, source=""):
    if element.category.upper() != "EQUIPMENT" or "ARMOR" not in element.tags:
        return None
    crew_ids, required = _crew_spec(unit, element)
    # Legacy implicit operators are not independently counted survivors. Do not invent people.
    if crew_ids is None:
        return None
    crews = [unit.elements[eid] for eid in dict.fromkeys(crew_ids)
             if eid in unit.elements and unit.elements[eid].category.upper() == "PERSONNEL"
             and not unit.elements[eid].metadata.get("dismountable", False)]
    available = sum(max(0, crew.count) for crew in crews)
    # The damage resolver has already decremented the live vehicle count. Use the same
    # equal-share allocation as damaged-vehicle detachment for pooled, unassigned crews.
    crew_id_set = {crew.eid for crew in crews}
    remaining_vehicles = 0
    for provider in unit.elements.values():
        if provider.category.upper() != "EQUIPMENT" or provider.count <= 0:
            continue
        provider_ids, _ = _crew_spec(unit, provider)
        if provider_ids is not None and crew_id_set.intersection(provider_ids):
            remaining_vehicles += provider.count
    needed = min(required, available // (remaining_vehicles + 1))
    if remaining_vehicles == 0:
        needed = available
    if needed <= 0:
        return None

    serial = int(unit.metadata.get("_crew_escape_serial", 0)) + 1
    uid = f"{unit.uid}-CREW-{serial}"
    while uid in sim.units:
        serial += 1
        uid = f"{unit.uid}-CREW-{serial}"
    unit.metadata["_crew_escape_serial"] = serial
    moved = {}
    for crew in crews:
        take = min(max(0, crew.count), needed)
        if not take:
            continue
        survivor = transfer_personnel(crew, take)
        survivor.metadata.pop("protected_by", None)
        moved[crew.eid] = survivor
        needed -= take

    typ = UnitType(
        name="DISMOUNTED_VEHICLE_CREW", branch="INFANTRY",
        max_speed_mps=float(unit.unit_type.metadata.get("crew_dismount_speed_mps", 1.8)),
        detection_range_m=sim.dismounted_crew_sensor["detection_range_m"], elements=[],
        metadata={"visual_sensor": copy.deepcopy(sim.dismounted_crew_sensor["visual_sensor"]),
                  "mobility_class": "FOOT", "mobility_capabilities": [],
                  "formation_family": "INF", "echelon": "TEAM",
                  "terrain_speed_factors": {"OPEN": 0.6, "ROAD": 1.0, "BRIDGE": 0.95}})
    child = Unit(
        uid=uid, name=f"{unit.name} surviving crew {serial}", side=unit.side,
        echelon="TEAM", unit_type=typ, pos=tuple(unit.pos), heading_deg=unit.heading_deg,
        watch_heading_deg=unit.watch_heading_deg, parent_id=unit.uid, elements=moved,
        local_tracks=copy.deepcopy(unit.local_tracks),
        metadata={"escaped_from": unit.uid, "destroyed_vehicle_element": element.eid,
                  "destroyed_vehicle_item_index": item_index, "mount_state": "DISMOUNTED"})
    sim.add_unit(child)
    unit.children.append(uid)
    # A HOLD order enables the existing unarmed-unit withdrawal doctrine; an orderless
    # entity would remain IDLE without ever consulting that doctrine.
    sim.issue_order(uid, Order(order_id=f"{uid}-RECOVER", kind="HOLD"))
    sim.log("CREW_DISMOUNTED", unit=unit.uid, child=uid, element=element.eid,
            item_index=item_index, personnel=child.personnel,
            parent_personnel=unit.personnel, pos=child.pos, reason="VEHICLE_DESTROYED")
    if unit.current_strength <= 0:
        unit.state = UnitState.DESTROYED
        sim.log("DESTROYED", unit=unit.uid, source=source)
    return child
