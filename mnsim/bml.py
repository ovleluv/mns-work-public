from __future__ import annotations
import math
from typing import Any, Dict, Iterable
from .model import Order, Unit


class ConditionEvaluator:
    """Deterministic BML-lite condition language.

    Conditions are intentionally based on simulation state available to the commanded formation;
    enemy ground truth is not exposed here (enemy counts come from the unit's own Tracks).  Active-order ``conditions`` are reactive triggers:
    when all become true, ``on_true`` replaces the current order.
    """
    OPS = {
        "<": lambda a,b: a < b,
        "<=": lambda a,b: a <= b,
        ">": lambda a,b: a > b,
        ">=": lambda a,b: a >= b,
        "==": lambda a,b: a == b,
        "!=": lambda a,b: a != b,
    }

    @classmethod
    def eval_all(cls, sim, unit: Unit, conditions: Iterable[Dict[str, Any]]) -> bool:
        return all(cls.eval_one(sim, unit, c) for c in conditions)

    @classmethod
    def eval_one(cls, sim, unit: Unit, cond: Dict[str, Any]) -> bool:
        lhs = cls.resolve(sim, unit, str(cond["lhs"]))
        rhs = cond["rhs"]
        op = str(cond.get("op", ">="))
        if op not in cls.OPS:
            raise ValueError(f"unsupported condition operator: {op}")
        return bool(cls.OPS[op](lhs, rhs))

    @staticmethod
    def resolve(sim, unit: Unit, path: str):
        if path == "self.loss_ratio": return unit.loss_ratio
        if path == "self.strength_ratio": return unit.strength_ratio
        if path == "self.personnel": return unit.personnel
        if path == "self.initial_personnel": return unit.initial_personnel
        if path == "self.equipment": return unit.equipment
        if path == "self.initial_equipment": return unit.initial_equipment
        if path == "self.state": return unit.state.value
        if path == "self.time_in_order":
            return max(0.0, sim.time - float(unit.metadata.get("order_started_t", sim.time)))
        if path.startswith("self.capability."):
            return unit.capability_available(path.split(".", 2)[2])
        if path == "sim.time": return sim.time
        if path == "self.enemy_count_near":
            # Perceived enemy formations only: current (non-LOST, non-DESTROYED) tracks whose
            # *estimated* position lies within the radius.  Never counts ground-truth units.
            r = float(unit.metadata.get("condition_radius_m", 800.0))
            max_age=float(sim.combat_config.get("track_lost_s",45.0))
            min_conf=float(sim.combat_config.get("track_action_confidence",0.35))
            # Preserve the legacy condition name, but never inspect live enemy positions or
            # survival. This is a count of currently usable perceived contacts, not truth.
            return sum(1 for tid,tr in unit.local_tracks.items()
                       if tr.actionable(sim.time,max_age,min_conf)
                       and (sim.units.get(tid) is None or sim.units[tid].side!=unit.side)
                       and math.dist(unit.pos,tr.estimated_pos)<=r)
        if path in ("self.distance_to_objective", "self.at_objective"):
            obj = unit.metadata.get("objective")
            if not obj:
                return False if path == "self.at_objective" else 1e12
            dx, dy = unit.pos[0]-obj[0], unit.pos[1]-obj[1]
            d = (dx*dx + dy*dy) ** 0.5
            if path == "self.at_objective":
                return d <= float(unit.metadata.get("condition_objective_tolerance_m", 10.0))
            return d
        raise ValueError(f"unsupported condition path: {path}")


def _schedule_fields(raw: Dict[str, Any]):
    start = raw.get("start_at_s", raw.get("not_before_s"))
    deadline = raw.get("deadline_s", raw.get("complete_by_s"))
    return (
        None if start is None else float(start),
        None if deadline is None else float(deadline),
    )


def parse_order(raw: Dict[str, Any]) -> Order:
    """Parse the engine-level order schema (legacy ``orders_by_unit`` remains supported)."""
    start, deadline = _schedule_fields(raw)
    return Order(
        order_id=str(raw.get("id", raw.get("kind", "order"))),
        kind=str(raw["kind"]).upper(),
        params=dict(raw.get("params", {})),
        conditions=list(raw.get("conditions", [])),
        on_true=raw.get("on_true"),
        on_false=raw.get("on_false"),
        directives=dict(raw.get("directives", {})),
        phase_id=None if raw.get("phase_id") is None else str(raw.get("phase_id")),
        start_at_s=start,
        deadline_s=deadline,
        on_deadline=raw.get("on_deadline"),
    )


MISSION_TASKS = {
    "MOVE_TO", "ATTACK_POSITION", "ATTACK_UNIT", "DESTROY_UNIT",
    "DEFEND_POSITION", "DEFEND_AREA", "SECURE_AREA", "SEIZE", "HOLD", "WITHDRAW",
    "DISMOUNT", "MOUNT", "BOARD", "DISEMBARK", "ENTER_BUILDING", "EXIT_BUILDING",
    "ATTACK_STRUCTURE", "STRIKE_INFRASTRUCTURE", "BUILD_BARRICADE",
}

ENGINE_ORDER_KINDS = {
    "MOVE", "ATTACK", "RETREAT", "ATTACK_UNIT", "DESTROY_UNIT", "DEFEND_AREA",
    "DEFEND", "HOLD", "WAIT", "DISMOUNT", "MOUNT", "BOARD", "DISEMBARK",
    "ENTER_BUILDING", "EXIT_BUILDING", "ATTACK_STRUCTURE", "STRIKE_INFRASTRUCTURE",
    "BUILD_BARRICADE",
}

SUPPORTED_DIRECTIVES = {
    "hold_at_all_costs", "allow_withdrawal", "allow_break_contact",
    "allow_artillery_displacement", "allow_indirect_fire_dispersion",
    "engagement_range_policy", "engagement_range_fraction",
}
BOOLEAN_DIRECTIVES = SUPPORTED_DIRECTIVES-{"engagement_range_policy","engagement_range_fraction"}


def compile_mission(sim, mission: Dict[str, Any]) -> tuple[str, Order]:
    """Compile an external BML mission into the engine Order representation.

    BML remains the command layer. Entity identity never grants position knowledge: runtime pursuit
    uses perceived Tracks, or an explicit location/intelligence reference supplied by the BML itself.
    """
    uid = str(mission["unit"])
    if uid not in sim.units:
        raise KeyError(f"BML mission references unknown unit {uid!r}")
    task = str(mission.get("task", mission.get("kind", "HOLD"))).upper()
    if task not in MISSION_TASKS:
        # Engine orders are allowed only through the explicit legacy kind schema.
        if "task" not in mission and "kind" in mission:
            return uid, parse_order(mission)
        raise ValueError(f"unsupported BML mission task: {task}")
    mid = str(mission.get("id", f"BML-{uid}-{task}"))
    params = dict(mission.get("params", {}))

    if task == "MOVE_TO":
        task = "MOVE"
        params.setdefault("destination", mission.get("destination"))
    elif task == "ATTACK_POSITION":
        task = "ATTACK"
        params.setdefault("destination", mission.get("destination"))
        params.setdefault("persistent", bool(mission.get("persistent", params.get("persistent", True))))
        if "search_hold_s" in mission:
            params.setdefault("search_hold_s", float(mission["search_hold_s"]))
    elif task in ("ATTACK_UNIT", "DESTROY_UNIT"):
        target = str(mission.get("target", params.get("target_unit", "")))
        if target not in sim.units:
            raise KeyError(f"BML {task} references unknown target unit {target!r}")
        if not sim.units[target].active:
            raise ValueError(f"BML {task} targets inactive aggregated unit {target!r}")
        params["target_unit"] = target
        # IMPORTANT: target identity is not target location. Never derive a search reference from
        # sim.units[target].pos here; that is Ground Truth and would bypass Track/Belief FoW.
        # A commander may explicitly supply a planning/intelligence location if desired.
        explicit_ref = mission.get(
            "target_position",
            mission.get("last_known_position", params.get("target_position", params.get("last_known_position")))
        )
        if explicit_ref is not None:
            params["search_reference"] = list(explicit_ref)
        params.pop("initial_target_pos", None)
        params.setdefault("persistent", True)
    elif task == "STRIKE_INFRASTRUCTURE":
        targets=list(mission.get("targets",params.get("targets",[])))
        if not targets and mission.get("target") is not None:targets=[mission.get("target")]
        if not targets:raise ValueError(f"BML STRIKE_INFRASTRUCTURE for {uid} requires targets")
        params["targets"]=[str(x) for x in targets]
        params.setdefault("coordinate_error_m",float(mission.get("coordinate_error_m",3.0)))
    elif task == "BUILD_BARRICADE":
        pos=mission.get("position",mission.get("destination",params.get("position")))
        if pos is None:raise ValueError(f"BML BUILD_BARRICADE for {uid} requires position/destination")
        params["position"]=list(pos)
        params["heading_deg"]=float(mission.get("heading_deg",params.get("heading_deg",0.0)))%360.0
        from .terrain import BARRICADE_LENGTH_M
        params["length_m"]=BARRICADE_LENGTH_M
        params.setdefault("construction_time_s",float(mission.get("construction_time_s",1200.0)))
    elif task == "ENTER_BUILDING":
        sid=str(mission.get("target_structure",mission.get("building",mission.get("target",params.get("target_structure","")))))
        b=sim.terrain.building_by_id(sid) if getattr(sim,"terrain",None) and hasattr(sim.terrain,"building_by_id") else None
        if b is None:raise KeyError(f"BML ENTER_BUILDING references unknown building {sid!r}")
        if not sim.terrain.building_operational(b):raise ValueError(f"BML ENTER_BUILDING references destroyed building {sid!r}")
        params["target_structure"]=sid
        params["destination"]=list(mission.get("destination",params.get("destination",sim.terrain.building_center(b))))
        task="ENTER_BUILDING"
    elif task == "EXIT_BUILDING":
        sid=str(mission.get("target_structure",mission.get("building",params.get("target_structure",""))))
        if not sid and getattr(sim,"terrain",None):
            cur=sim.terrain.building_at(sim.units[uid].pos); sid=str(cur.get("id","")) if cur else ""
        b=sim.terrain.building_by_id(sid) if sid and getattr(sim,"terrain",None) else None
        if b is None:raise ValueError(f"BML EXIT_BUILDING for {uid} requires the occupied building or target_structure")
        dest=mission.get("destination",params.get("destination"))
        if dest is None:raise ValueError(f"BML EXIT_BUILDING for {uid} requires destination outside the building")
        if sim.terrain.building_at(tuple(dest),include_destroyed=True) is b:raise ValueError("EXIT_BUILDING destination must be outside the target building")
        params["target_structure"]=sid; params["destination"]=list(dest); task="EXIT_BUILDING"
    elif task == "ATTACK_STRUCTURE":
        sid=str(mission.get("target_structure",mission.get("target",params.get("target_structure",""))))
        b=sim.terrain.building_by_id(sid) if getattr(sim,"terrain",None) and hasattr(sim.terrain,"building_by_id") else None
        if b is None:raise KeyError(f"BML ATTACK_STRUCTURE references unknown building {sid!r}")
        params["target_structure"]=sid
        task="ATTACK_STRUCTURE"
    elif task == "DISMOUNT":
        params.setdefault("destination", mission.get("destination"))
    elif task == "MOUNT":
        params.setdefault("destination", mission.get("destination"))
    elif task == "BOARD":
        carrier=str(mission.get("carrier",params.get("carrier","")))
        if carrier not in sim.units: raise KeyError(f"BML BOARD references unknown carrier {carrier!r}")
        params["carrier"]=carrier
    elif task == "DISEMBARK":
        params.setdefault("passenger", mission.get("passenger", mission.get("passengers","ALL")))
    elif task == "DEFEND_POSITION":
        task = "DEFEND_AREA"
        center = mission.get("center", mission.get("destination", list(sim.units[uid].pos)))
        params.setdefault("center", center)
        params.setdefault("radius_m", float(mission.get("radius_m", 120.0)))
    elif task in ("DEFEND_AREA", "SECURE_AREA"):
        secure = task == "SECURE_AREA"
        task = "DEFEND_AREA"
        area = dict(mission.get("area", {}))
        if "center" in mission: area.setdefault("center", mission["center"])
        if "radius_m" in mission: area.setdefault("radius_m", mission["radius_m"])
        if "polygon" in mission: area.setdefault("polygon", mission["polygon"])
        params.update({k:v for k,v in area.items() if k not in params})
        if not params.get("center") and not params.get("polygon"):
            raise ValueError(f"BML {('SECURE_AREA' if secure else 'DEFEND_AREA')} for {uid} requires center/radius or polygon")
        # Area defence is bounded pursuit, not an unrestricted ATTACK.  The formation may react
        # to LOCAL/SHARED tracks, but never uses Ground Truth to chase an unseen unit.
        params.setdefault("react_to_contacts", bool(mission.get("react_to_contacts", True)))
        params.setdefault("pursue_within_area", bool(mission.get("pursue_within_area", secure)))
        if "engagement_radius_m" in mission:
            params.setdefault("engagement_radius_m", float(mission["engagement_radius_m"]))
        if "pursuit_radius_m" in mission:
            params.setdefault("pursuit_radius_m", float(mission["pursuit_radius_m"]))
        params.setdefault("return_to_center", bool(mission.get("return_to_center", secure)))
    elif task == "SEIZE":
        task = "ATTACK"
        destination = mission.get("destination")
        objective = mission.get("objective")
        if destination is None and objective is not None:
            destination = sim.objectives.get(str(objective))
        if destination is None:
            raise ValueError(f"BML SEIZE for {uid} requires destination or known objective")
        params.setdefault("destination", destination)
        params.setdefault("persistent", True)
    elif task == "HOLD":
        params.setdefault("duration_s", float(mission.get("duration_s", -1)))
    elif task == "WITHDRAW":
        task = "RETREAT"
        params.setdefault("destination", mission.get("destination"))

    if params.get("destination") is None and task in ("MOVE", "ATTACK", "RETREAT"):
        raise ValueError(f"BML {task} for {uid} requires destination")

    start, deadline = _schedule_fields(mission)
    return uid, Order(
        order_id=mid,
        kind=task,
        params=params,
        conditions=list(mission.get("conditions", [])),
        on_true=mission.get("on_true"),
        on_false=mission.get("on_false"),
        directives=dict(mission.get("directives", {})),
        phase_id=None if mission.get("phase_id") is None else str(mission.get("phase_id")),
        start_at_s=start,
        deadline_s=deadline,
        on_deadline=mission.get("on_deadline"),
    )


def compile_order_fragment(sim, unit: Unit, raw: Dict[str, Any]) -> Order:
    """Compile an on_true/on_deadline branch; mission fragments inherit the commanded unit."""
    frag=dict(raw)
    if "task" in frag:
        frag.setdefault("unit", unit.uid)
        uid, order=compile_mission(sim, frag)
        if uid != unit.uid:
            raise ValueError("conditional branch cannot redirect command to another unit")
        return order
    return parse_order(frag)


def validate_order_tree(sim, unit: Unit, order: Order):
    """Validate a complete BML order and its conditional branches before changing live queues."""
    if order.kind not in ENGINE_ORDER_KINDS:
        raise ValueError(f"unsupported engine order kind: {order.kind}")
    if order.kind in {"MOVE","ATTACK","RETREAT"} and order.params.get("destination") is None:
        raise ValueError(f"{order.kind} requires a destination")
    if order.kind in {"ATTACK_UNIT","DESTROY_UNIT"}:
        target=str(order.params.get("target_unit", ""))
        if target not in sim.units:
            raise KeyError(f"{order.kind} references unknown target unit {target!r}")
        if not sim.units[target].active:
            raise ValueError(f"{order.kind} targets inactive aggregated unit {target!r}")
    unknown=set(order.directives)-SUPPORTED_DIRECTIVES
    if unknown:
        raise ValueError(f"unsupported BML directives: {sorted(unknown)}")
    for key in BOOLEAN_DIRECTIVES & order.directives.keys():
        if not isinstance(order.directives[key],bool):
            raise ValueError(f"{key} must be a boolean")
    if "engagement_range_fraction" in order.directives:
        fraction=float(order.directives["engagement_range_fraction"])
        if not math.isfinite(fraction) or fraction<=0.0:
            raise ValueError("engagement_range_fraction must be finite and positive")
    if "engagement_range_policy" in order.directives:
        policy=str(order.directives["engagement_range_policy"]).upper()
        if policy not in {"STANDOFF","BALANCED","MIDRANGE","COMBINED_ARMS","AGGRESSIVE","CLOSE_TO_ALL_WEAPONS"}:
            raise ValueError(f"unsupported engagement_range_policy: {policy}")
    for field_name, value in (("start_at_s",order.start_at_s),("deadline_s",order.deadline_s)):
        if value is not None and not math.isfinite(float(value)):
            raise ValueError(f"{field_name} must be finite")
    for field_name in ("destination","center","position","search_reference","target_position"):
        value=order.params.get(field_name)
        if value is None:
            continue
        order.params[field_name]=list(validate_order_tree_position(sim,value,field_name))
    polygon=order.params.get("polygon")
    if polygon:
        if not isinstance(polygon,(list,tuple)) or len(polygon)<3:
            raise ValueError("polygon must contain at least three positions")
        order.params["polygon"]=[list(validate_order_tree_position(sim,point,"polygon point"))
                                 for point in polygon]
    for cond in order.conditions:
        if not isinstance(cond,dict):
            raise ValueError("BML conditions must be objects")
        lhs=ConditionEvaluator.resolve(sim,unit,str(cond["lhs"]))
        op=str(cond.get("op",">="))
        if op not in ConditionEvaluator.OPS:
            raise ValueError(f"unsupported condition operator: {op}")
        try:
            ConditionEvaluator.OPS[op](lhs,cond["rhs"])
        except TypeError as exc:
            raise ValueError(f"incompatible condition operands for {cond['lhs']}") from exc
    for branch in (order.on_true,order.on_false,order.on_deadline):
        if branch is not None:
            validate_order_tree(sim,unit,compile_order_fragment(sim,unit,branch))


def validate_order_tree_position(sim, point, field_name: str):
    try:
        x,y=map(float,point)
    except (TypeError,ValueError) as exc:
        raise ValueError(f"{field_name} must be a two-dimensional position") from exc
    if (not math.isfinite(x) or not math.isfinite(y)
            or not 0.0<=x<=float(sim.world["width_m"])
            or not 0.0<=y<=float(sim.world["height_m"])):
        raise ValueError(f"{field_name} is outside the scenario world: {(x,y)}")
    return x,y


def apply_branch(sim, unit: Unit, order: Order):
    """Legacy completion-time true/false branch retained for backward compatibility."""
    if not order.conditions:
        return
    branch = order.on_true if ConditionEvaluator.eval_all(sim, unit, order.conditions) else order.on_false
    if branch:
        unit.order_queue.insert(0, compile_order_fragment(sim, unit, branch))


def _validate_side(sim, uid: str, expected_side: str | None):
    if expected_side and sim.units[uid].side.value != str(expected_side).upper():
        raise ValueError(f"BML for {expected_side} attempts to command {uid} ({sim.units[uid].side.value})")
    if not sim.units[uid].active:
        raise ValueError(f"BML cannot command inactive aggregated unit {uid!r}")


def _inherit_phase_fields(mission: Dict[str, Any], phase: Dict[str, Any]) -> Dict[str, Any]:
    out=dict(mission)
    out.setdefault("phase_id", str(phase.get("id", "PHASE")))
    for key in ("start_at_s", "not_before_s", "deadline_s", "complete_by_s"):
        if key in phase and key not in out:
            out[key]=phase[key]
    directives=dict(phase.get("directives", {}))
    directives.update(dict(out.get("directives", {})))
    if directives:
        out["directives"]=directives
    return out


def apply_bml_document(sim, raw: Dict[str, Any], expected_side: str | None = None):
    """Load legacy orders, flat missions, and phase-organized mission plans.

    Phase lists are intentionally compiled into the same per-unit Order queue rather than a second
    simulation engine.  Without ``start_at_s`` each unit proceeds to its next queued phase when its
    previous mission completes; ``start_at_s`` provides an absolute scenario-time gate.
    """
    from .validation import validate_bml_document
    validate_bml_document(raw, getattr(sim, "world", None))
    declared = str(raw.get("side", expected_side or "")).upper()
    if expected_side and declared and declared != str(expected_side).upper():
        raise ValueError(f"BML side mismatch: expected {expected_side}, file declares {declared}")

    # Compile and validate the complete document before touching any live orders. A typo in a
    # later mission must not leave earlier formations with only half of a replacement plan.
    compiled=[]
    for uid, raw_orders in dict(raw.get("orders_by_unit", {})).items():
        if uid not in sim.units:
            raise KeyError(f"BML references unknown unit {uid!r}")
        _validate_side(sim,uid,declared)
        for ro in raw_orders:
            compiled.append((uid,parse_order(ro)))

    missions=list(raw.get("missions", []))
    for phase in raw.get("phases", []):
        if "id" not in phase:
            raise ValueError("BML phase requires an id")
        for mission in phase.get("missions", []):
            missions.append(_inherit_phase_fields(mission, phase))

    for mission in missions:
        uid, order = compile_mission(sim, mission)
        _validate_side(sim,uid,declared)
        compiled.append((uid,order))

    for uid, order in compiled:
        validate_order_tree(sim,sim.units[uid],order)

    replaced=set()
    replace_existing=bool(raw.get("replace_existing_orders", True))
    for uid, order in compiled:
        if replace_existing and uid not in replaced:
            sim.units[uid].order_queue.clear()
            sim.units[uid].current_order=None
            # Mid-run re-tasking must not inherit the old order's route, timers or permissions.
            if hasattr(sim,"reset_order_execution_state"):
                sim.reset_order_execution_state(sim.units[uid])
            else:
                sim.units[uid].metadata.pop("order_started_t",None)
            replaced.add(uid)
        sim.issue_order(uid, order)
