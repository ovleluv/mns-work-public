"""Validation of untrusted scenario / terrain / BML input.

Scenario, terrain and BML documents may be produced by people, editors or LLMs.  They are data,
never code, but malformed data can still hang or crash the engine: a destination at 1e13 m makes
path sampling allocate billions of points, ``NaN`` coordinates raise deep inside navigation, and a
condition with a string right-hand side only fails at the first tick.  Everything here runs at
load time so the failure is a clear ``ValueError`` naming the offending field.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Iterable, Sequence

#: Largest JSON document the loaders accept (bytes).
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
#: Largest world edge accepted (metres).
MAX_WORLD_EDGE_M = 1_000_000.0
#: Terrain complexity limits; the sparse planner is quadratic in vertex count.
MAX_FEATURE_VERTICES = 5_000
MAX_TERRAIN_VERTICES = 60_000
#: Maximum nesting of conditional branches (on_true / on_false / on_deadline).
MAX_BRANCH_DEPTH = 16

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Coordinates may lie slightly outside the authored world (off-map support positions, edge
#: withdrawals) but not arbitrarily far: this fraction of the larger world edge is tolerated.
WORLD_MARGIN_FRACTION = 0.25


def world_margin(world: dict | None) -> float:
    if not world:
        return 0.0
    return WORLD_MARGIN_FRACTION * max(float(world.get("width_m", 0.0)), float(world.get("height_m", 0.0)))


class ValidationError(ValueError):
    """Input document is malformed or outside the engine's supported envelope."""


# ---------------------------------------------------------------- JSON -------------------------

def _reject_constant(name: str):
    raise ValidationError(f"non-finite JSON number {name!r} is not allowed")


def strict_json_loads(text: str) -> Any:
    """``json.loads`` that rejects NaN/Infinity (Python's default accepts them)."""
    return json.loads(text, parse_constant=_reject_constant)


def read_json_file(path: Path | str) -> Any:
    p = Path(path)
    size = p.stat().st_size
    if size > MAX_DOCUMENT_BYTES:
        raise ValidationError(f"{p.name}: document is {size} bytes (limit {MAX_DOCUMENT_BYTES})")
    try:
        return strict_json_loads(p.read_text(encoding="utf-8"))
    except ValidationError as ex:
        raise ValidationError(f"{p}: {ex}") from None


# ---------------------------------------------------------------- resource paths ---------------

def allowed_resource_roots(scenario_path: str | Path, extra: Iterable[str | Path] = ()) -> list[Path]:
    """Directories a scenario may reference: its own folder tree and the project tree.

    Additional roots can be granted with the ``MNSIM_RESOURCE_ROOTS`` environment variable
    (``os.pathsep``-separated) or by the caller.
    """
    roots = [Path(scenario_path).resolve().parent, PROJECT_ROOT]
    env = os.environ.get("MNSIM_RESOURCE_ROOTS", "")
    roots += [Path(x).resolve() for x in env.split(os.pathsep) if x.strip()]
    roots += [Path(x).resolve() for x in extra]
    return roots


def is_within(path: Path, roots: Sequence[Path]) -> bool:
    rp = path.resolve()
    for root in roots:
        try:
            rp.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def display_path(path: str | Path) -> str:
    """Project-relative path for logs/replays (avoids leaking user names / folder layout)."""
    p = Path(path).resolve()
    try:
        return p.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return p.name


# ---------------------------------------------------------------- numbers / points -------------

def finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field}: expected a number, got {type(value).__name__}")
    v = float(value)
    if not math.isfinite(v):
        raise ValidationError(f"{field}: must be finite, got {value!r}")
    return v


def point(value: Any, field: str, world: dict | None = None, margin_m: float | None = None) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise ValidationError(f"{field}: expected [x, y], got {value!r}")
    x = finite_number(value[0], f"{field}[0]")
    y = finite_number(value[1], f"{field}[1]")
    if world:
        if margin_m is None:
            margin_m = world_margin(world)
        w = float(world.get("width_m", 0.0)); h = float(world.get("height_m", 0.0))
        if w > 0 and h > 0 and not (-margin_m <= x <= w + margin_m and -margin_m <= y <= h + margin_m):
            raise ValidationError(f"{field}: {x:g},{y:g} is outside the {w:g} x {h:g} m world")
    return x, y


def polygon(value: Any, field: str, world: dict | None = None, margin_m: float | None = None,
            max_vertices: int = MAX_FEATURE_VERTICES) -> list:
    if not isinstance(value, (list, tuple)):
        raise ValidationError(f"{field}: expected a list of points")
    if len(value) > max_vertices:
        raise ValidationError(f"{field}: {len(value)} vertices (limit {max_vertices})")
    return [point(p, f"{field}[{i}]", world, margin_m) for i, p in enumerate(value)]


# ---------------------------------------------------------------- world / terrain --------------

def validate_world(world: Any) -> dict:
    if not isinstance(world, dict):
        raise ValidationError("world: expected an object")
    out = dict(world)
    for key in ("width_m", "height_m"):
        v = finite_number(out.get(key, 4000.0), f"world.{key}")
        if not 0.0 < v <= MAX_WORLD_EDGE_M:
            raise ValidationError(f"world.{key}: {v:g} outside (0, {MAX_WORLD_EDGE_M:g}]")
        out[key] = v
    return out


def validate_terrain(data: Any, source: str = "terrain") -> dict:
    """Check geometry is finite and bounded in complexity (positions may lie off-map)."""
    if not isinstance(data, dict):
        raise ValidationError(f"{source}: expected an object")
    total = 0
    for group, key in (("roads", "points"), ("rivers", "points"), ("rivers", "polygon"),
                       ("bridges", "points"), ("areas", "polygon"), ("observation_zones", "polygon")):
        for i, feature in enumerate(data.get(group, []) or []):
            if not isinstance(feature, dict):
                raise ValidationError(f"{source}.{group}[{i}]: expected an object")
            pts = feature.get(key)
            if pts is None:
                continue
            polygon(pts, f"{source}.{group}[{i}].{key}")
            total += len(pts)
    for i, br in enumerate(data.get("bridges", []) or []):
        if "center" in br:
            point(br["center"], f"{source}.bridges[{i}].center")
    for i, wall in enumerate(data.get("barricades", []) or []):
        point(wall.get("center", (0, 0)), f"{source}.barricades[{i}].center")
    if total > MAX_TERRAIN_VERTICES:
        raise ValidationError(f"{source}: {total} vertices in total (limit {MAX_TERRAIN_VERTICES})")
    return data


# ---------------------------------------------------------------- BML / orders ----------------

NUMERIC_CONDITION_PATHS = {
    "self.loss_ratio", "self.strength_ratio", "self.personnel", "self.initial_personnel",
    "self.equipment", "self.initial_equipment", "self.time_in_order", "sim.time",
    "self.enemy_count_near", "self.distance_to_objective",
}
BOOL_CONDITION_PATHS = {"self.at_objective"}
STRING_CONDITION_PATHS = {"self.state"}
CONDITION_OPS = {"<", "<=", ">", ">=", "==", "!="}

_POINT_FIELDS = ("destination", "center", "position", "target_position", "last_known_position",
                 "search_reference", "initial_target_pos")
_POLYGON_FIELDS = ("polygon",)


def validate_condition(cond: Any, field: str) -> None:
    if not isinstance(cond, dict):
        raise ValidationError(f"{field}: expected an object")
    if "lhs" not in cond or "rhs" not in cond:
        raise ValidationError(f"{field}: requires lhs and rhs")
    lhs = str(cond["lhs"]); rhs = cond["rhs"]; op = str(cond.get("op", ">="))
    if op not in CONDITION_OPS:
        raise ValidationError(f"{field}.op: unsupported operator {op!r}")
    if lhs in NUMERIC_CONDITION_PATHS:
        finite_number(rhs, f"{field}.rhs")
    elif lhs in BOOL_CONDITION_PATHS or lhs.startswith("self.capability."):
        if not isinstance(rhs, bool):
            raise ValidationError(f"{field}.rhs: {lhs} compares against true/false")
        if op not in ("==", "!="):
            raise ValidationError(f"{field}.op: {lhs} supports only == and !=")
    elif lhs in STRING_CONDITION_PATHS:
        if not isinstance(rhs, str):
            raise ValidationError(f"{field}.rhs: {lhs} compares against a state name string")
        if op not in ("==", "!="):
            raise ValidationError(f"{field}.op: {lhs} supports only == and !=")
    else:
        raise ValidationError(f"{field}.lhs: unsupported condition path {lhs!r}")


def _validate_geometry_fields(obj: dict, field: str, world: dict | None) -> None:
    for key in _POINT_FIELDS:
        if obj.get(key) is not None:
            point(obj[key], f"{field}.{key}", world)
    for key in _POLYGON_FIELDS:
        if obj.get(key) is not None:
            polygon(obj[key], f"{field}.{key}", world)
    area = obj.get("area")
    if isinstance(area, dict):
        _validate_geometry_fields(area, f"{field}.area", world)
    for key in ("radius_m", "engagement_radius_m", "pursuit_radius_m", "duration_s",
                "start_at_s", "not_before_s", "deadline_s", "complete_by_s", "heading_deg",
                "construction_time_s", "search_hold_s", "coordinate_error_m"):
        if obj.get(key) is not None:
            finite_number(obj[key], f"{field}.{key}")


def validate_order_like(raw: Any, field: str, world: dict | None = None, depth: int = 0) -> None:
    """Validate a mission/order dict and, recursively, every conditional branch it contains."""
    if depth > MAX_BRANCH_DEPTH:
        raise ValidationError(f"{field}: conditional branches nested deeper than {MAX_BRANCH_DEPTH}")
    if not isinstance(raw, dict):
        raise ValidationError(f"{field}: expected an object")
    _validate_geometry_fields(raw, field, world)
    for key in ("params", "directives"):
        if key in raw and not isinstance(raw[key], dict):
            raise ValidationError(f"{field}.{key}: expected an object")
    params = raw.get("params")
    if params is not None:
        _validate_geometry_fields(params, f"{field}.params", world)
    conditions = raw.get("conditions", [])
    if not isinstance(conditions, list):
        raise ValidationError(f"{field}.conditions: expected a list")
    for i, cond in enumerate(conditions):
        validate_condition(cond, f"{field}.conditions[{i}]")
    for key in ("on_true", "on_false", "on_deadline"):
        if raw.get(key) is not None:
            validate_order_like(raw[key], f"{field}.{key}", world, depth + 1)


def validate_bml_document(raw: Any, world: dict | None, source: str = "BML") -> None:
    if not isinstance(raw, dict):
        raise ValidationError(f"{source}: expected an object")
    for uid, orders in dict(raw.get("orders_by_unit", {})).items():
        if not isinstance(orders, list):
            raise ValidationError(f"{source}.orders_by_unit.{uid}: expected a list")
        for i, o in enumerate(orders):
            validate_order_like(o, f"{source}.orders_by_unit.{uid}[{i}]", world)
    for i, m in enumerate(raw.get("missions", []) or []):
        validate_order_like(m, f"{source}.missions[{i}]", world)
    for j, phase in enumerate(raw.get("phases", []) or []):
        if not isinstance(phase, dict):
            raise ValidationError(f"{source}.phases[{j}]: expected an object")
        _validate_geometry_fields(phase, f"{source}.phases[{j}]", world)
        for i, m in enumerate(phase.get("missions", []) or []):
            validate_order_like(m, f"{source}.phases[{j}].missions[{i}]", world)


def validate_scenario_units(units: Any, world: dict) -> None:
    if not isinstance(units, list):
        raise ValidationError("units: expected a list")
    seen = set()
    for i, r in enumerate(units):
        if not isinstance(r, dict):
            raise ValidationError(f"units[{i}]: expected an object")
        uid = r.get("id")
        if not isinstance(uid, str) or not uid:
            raise ValidationError(f"units[{i}].id: expected a non-empty string")
        if uid in seen:
            raise ValidationError(f"units[{i}].id: duplicate unit id {uid!r}")
        seen.add(uid)
        point(r.get("pos"), f"units[{i}].pos ({uid})", world)
        for key in ("heading_deg", "watch_heading_deg"):
            if r.get(key) is not None:
                finite_number(r[key], f"units[{i}].{key}")
        for j, o in enumerate(r.get("orders", []) or []):
            validate_order_like(o, f"units[{i}].orders[{j}]", world)
