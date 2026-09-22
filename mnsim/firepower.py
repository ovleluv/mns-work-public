"""Personnel-driven firing participation, shared by direct and indirect fire.

Rates describe one shooter or one fully staffed weapon. Inventory is a physical
limit, never a firepower multiplier. Separate crew pools are allocated once per
provider element, so several guns cannot each claim the same surviving operators.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Firepower:
    participants: int
    operators: int
    reason: str = "READY"


def _inventory(weapon, fallback):
    # weapon_count is the new inventory spelling; old editor files remain readable.
    return max(0, int(weapon.metadata.get('weapon_count',
                                        weapon.metadata.get('system_count', fallback))))


def _crew_spec(unit, element):
    md = element.metadata
    explicit = md.get('crew_elements')
    if explicit is not None:
        ids = [explicit] if isinstance(explicit, str) else list(explicit)
    else:
        role = md.get('crew_role')
        if role is None and any(w.capability.upper() == 'INDIRECT_FIRE' for w in element.weapons):
            role = 'ARTILLERY_CREW'
        if role is None and any(e.category.upper() == 'PERSONNEL' and e.role.upper() == 'CREW'
                                for e in unit.elements.values()):
            role = 'CREW'
        ids = [e.eid for e in unit.elements.values()
               if role and e.category.upper() == 'PERSONNEL' and e.role.upper() == str(role).upper()]
        # Artillery is crew served even when its declared crew pool is missing.
        if role is None:
            return None, max(1, int(md.get('crew_per_weapon', md.get('crew', 1))))
    indirect = any(w.capability.upper() == 'INDIRECT_FIRE' for w in element.weapons)
    required = max(1, int(md.get('crew_per_weapon', md.get('crew', 5 if indirect else 1))))
    return ids, required


def _staffed_platforms(unit, requested):
    remaining = {e.eid: max(0, e.count) for e in unit.elements.values()
                 if e.category.upper() == 'PERSONNEL' and not e.metadata.get('dismountable', False)}
    # Stable priority for deliberately shared crew pools. Explicit crew_elements
    # should be used for organic batteries/vehicle sections with dedicated crews.
    for element in sorted(unit.elements.values(), key=lambda e: e.eid):
        if element.category.upper() != 'EQUIPMENT' or not element.weapons:
            continue
        ids, required = _crew_spec(unit, element)
        available = element.fire_capable_item_count
        if ids is None:
            # Legacy vehicle entities include their operators in the platform.
            # Loss of the platform removes them; explicit personnel overrides can
            # additionally model crew casualties without destroying the vehicle.
            operators = max(0, int(element.metadata.get('embedded_crew_remaining', available * required)))
            staffed = min(available, operators // required)
        else:
            ids = list(dict.fromkeys(ids))
            operators = sum(remaining.get(eid, 0) for eid in ids)
            staffed = min(available, operators // required)
            needed = staffed * required
            for eid in ids:
                take = min(remaining.get(eid, 0), needed)
                remaining[eid] = remaining.get(eid, 0) - take
                needed -= take
        if element is requested:
            return staffed, staffed * required
    return 0, 0


def _personnel_crew_requirement(element, weapon):
    model = str(weapon.metadata.get('inventory_model', '')).upper()
    crew_served = model == 'CREW_SERVED' or int(weapon.metadata.get('crew_per_weapon',
                                               weapon.metadata.get('operators_per_system', 1))) > 1
    required = element.min_operators
    if crew_served:
        required = weapon.metadata.get('crew_per_weapon',
                                       weapon.metadata.get('operators_per_system', required))
    required = max(1, int(required))
    return crew_served, required


def participation(unit, element, weapon):
    if not unit.alive or element.count <= 0 or not weapon.has_ammo:
        return Firepower(0, 0, 'UNAVAILABLE')
    if element.metadata.get('dismountable', False) and str(unit.metadata.get('mount_state', 'MOUNTED')).upper() == 'MOUNTED':
        return Firepower(0, 0, 'EMBARKED')
    model = str(weapon.metadata.get('inventory_model', '')).upper()
    if element.category.upper() == 'PERSONNEL':
        operators = max(0, element.count)
        crew_served, required = _personnel_crew_requirement(element, weapon)
        if crew_served:
            count = min(_inventory(weapon, max(1, element.initial_count // required)), operators // required)
        else:
            count = operators
            if model == 'INDIVIDUAL_ASSIGNED' or 'weapon_count' in weapon.metadata:
                count = min(count, _inventory(weapon, operators))
            # Explicit zero means an unequipped slot, even for legacy aggregate rifles.
            if _inventory(weapon, operators) == 0:
                count = 0
            if operators < max(1, element.min_operators):
                count = 0
        if model == 'DISPOSABLE_ROUNDS' and weapon.ammo_remaining >= 0:
            count = min(count, weapon.ammo_remaining)
        # A frontage limit is an explicit experiment/doctrine constraint, not inventory.
        count = min(count, max(0, int(weapon.metadata.get('max_engaged_operators', count))))
    else:
        staffed, operators = _staffed_platforms(unit, element)
        mounts = max(1, int(weapon.metadata.get('systems_per_provider', 1)))
        count = min(staffed * mounts, _inventory(weapon, element.initial_count * mounts))
    return Firepower(max(0, count), operators, 'READY' if count > 0 else 'NO_OPERATORS_OR_WEAPONS')


def crew_failure_reason(unit):
    """Formation-wide crew loss, independent of branch and target geometry.

    A surviving staffed weapon keeps a mixed formation operational. Unarmed sensor
    entities and legacy platforms with implicit crews are not disabled by default.
    Ammunition exhaustion alone is not crew loss. Physical existence (Unit.alive)
    is deliberately separate from this status.
    """
    if not unit.alive:
        return None
    if unit.initial_personnel > 0 and unit.personnel == 0:
        return 'NO_CREW'

    understaffed = False
    explicit_platforms = []
    for element in unit.elements.values():
        if element.category.upper() == 'EQUIPMENT' and element.count > 0:
            ids, _ = _crew_spec(unit, element)
            if ids is not None:
                operators = sum(max(0, unit.elements[eid].count) for eid in set(ids)
                                if eid in unit.elements
                                and unit.elements[eid].category.upper() == 'PERSONNEL'
                                and not unit.elements[eid].metadata.get('dismountable', False))
                explicit_platforms.append(operators)
            elif 'embedded_crew_remaining' in element.metadata:
                explicit_platforms.append(max(0, int(element.metadata['embedded_crew_remaining'])))
        for weapon in element.weapons:
            if element.metadata.get('dismountable', False) and str(unit.metadata.get('mount_state', 'MOUNTED')).upper() == 'MOUNTED':
                continue
            if participation(unit, element, weapon).participants > 0:
                return None
            if element.category.upper() == 'EQUIPMENT':
                mounts = max(1, int(weapon.metadata.get('systems_per_provider', 1)))
                if element.fire_capable_item_count > 0 and _inventory(weapon, element.initial_count * mounts) > 0:
                    staffed, _ = _staffed_platforms(unit, element)
                    understaffed |= staffed == 0
            else:
                crew_served, required = _personnel_crew_requirement(element, weapon)
                inventory = _inventory(weapon, max(1, element.initial_count // required) if crew_served else element.initial_count)
                understaffed |= inventory > 0 and element.count < required
    if explicit_platforms and not any(explicit_platforms):
        return 'NO_CREW'
    return 'INSUFFICIENT_WEAPON_CREW' if understaffed else None
