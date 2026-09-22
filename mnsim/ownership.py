"""Element ownership helpers used by formation and personnel transfers."""
import copy


def remap_element(element, ids):
    """Copy an element and its local references into a new unit namespace."""
    result = copy.deepcopy(element)
    result.eid = ids.get(element.eid, element.eid)
    protected = result.metadata.get("protected_by")
    if protected is not None:
        result.metadata["protected_by"] = ids.get(protected, protected)
    crews = result.metadata.get("crew_elements")
    if crews is not None:
        result.metadata["crew_elements"] = (ids.get(crews, crews) if isinstance(crews, str)
                                              else [ids.get(eid, eid) for eid in crews])
    return result


def transfer_personnel(element, count):
    """Move survivors and their share of personal inventory, retaining all remainders."""
    if not 0 < count <= element.count:
        raise ValueError("Personnel transfer must be within the surviving count")
    moved = copy.deepcopy(element)
    moved.count = moved.initial_count = count
    for original, transferred in zip(element.weapons, moved.weapons):
        for attr in ("ammo_remaining", "ammo_capacity"):
            value = getattr(original, attr)
            if value >= 0:
                share = value * count // element.count
                setattr(transferred, attr, share)
                setattr(original, attr, value - share)
        for key in ("weapon_count", "system_count"):
            if key in original.metadata:
                value = max(0, int(original.metadata[key]))
                share = value * count // element.count
                transferred.metadata[key] = share
                original.metadata[key] = value - share
    element.count -= count
    element.initial_count = max(element.count, element.initial_count - count)
    return moved
