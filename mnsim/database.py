from __future__ import annotations

"""External definition repositories for user-maintainable M&S data.

Flat performance/specification data is loaded from CSV. Hierarchical composition such as
formation templates and loadout profiles remains JSON. Runtime simulation objects never read
CSV directly; all references are resolved during scenario loading.
"""

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping


def _json_cell(value: str, default):
    if value is None or str(value).strip() == "":
        return default
    return json.loads(value)


def _int_cell(value: str, default: int) -> int:
    return default if value is None or str(value).strip() == "" else int(value)


def _float_cell(value: str, default: float) -> float:
    return default if value is None or str(value).strip() == "" else float(value)


@dataclass(frozen=True)
class WeaponCatalog:
    """Immutable raw weapon definitions keyed by stable weapon_id."""
    definitions: Dict[str, Dict[str, Any]]

    @classmethod
    def from_csv(cls, path: str | Path) -> "WeaponCatalog":
        path = Path(path)
        rows: Dict[str, Dict[str, Any]] = {}
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                wid = str(row.get("weapon_id", "")).strip()
                if not wid:
                    continue
                if wid in rows:
                    raise ValueError(f"Duplicate weapon_id {wid!r} in {path}")
                rows[wid] = {
                    "name": row["name"],
                    "capability": row.get("capability", "DIRECT_FIRE"),
                    "range_m": _float_cell(row.get("range_m"), 0.0),
                    "shots_per_min": _float_cell(row.get("shots_per_min"), 1.0),
                    "pk": _float_cell(row.get("pk"), 0.1),
                    "target_tags": _json_cell(row.get("target_tags"), ["PERSONNEL"]),
                    "max_effect_count": _int_cell(row.get("max_effect_count"), 1),
                    "ammo": _int_cell(row.get("ammo"), -1),
                    "metadata": _json_cell(row.get("metadata_json"), {}),
                }
        return cls(rows)

    def resolve(self, weapon_id: str) -> Dict[str, Any]:
        try:
            return dict(self.definitions[weapon_id])
        except KeyError as exc:
            raise KeyError(f"Unknown weapon_id {weapon_id!r}") from exc


@dataclass(frozen=True)
class LoadoutCatalog:
    """Named slot->weapon mappings. A profile can optionally inherit another profile."""
    profiles: Dict[str, Dict[str, str]]

    @classmethod
    def from_json(cls, path: str | Path) -> "LoadoutCatalog":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        src = dict(raw.get("loadouts", raw))
        resolved: Dict[str, Dict[str, str]] = {}

        def build(name: str, stack=()) -> Dict[str, str]:
            if name in resolved:
                return dict(resolved[name])
            if name in stack:
                raise ValueError(f"Loadout inheritance cycle: {' -> '.join((*stack, name))}")
            if name not in src:
                raise KeyError(f"Unknown loadout {name!r}")
            row = dict(src[name])
            slots: Dict[str, str] = {}
            parent = row.get("extends")
            if parent:
                slots.update(build(str(parent), (*stack, name)))
            slots.update({str(k): str(v) for k, v in dict(row.get("slots", {})).items()})
            resolved[name] = slots
            return dict(slots)

        for name in src:
            build(str(name))
        return cls(resolved)

    def get(self, name: str) -> Dict[str, str]:
        try:
            return dict(self.profiles[name])
        except KeyError as exc:
            raise KeyError(f"Unknown loadout {name!r}") from exc


@dataclass(frozen=True)
class PlatformCatalog:
    """Flat vehicle/platform definitions used by formation equipment elements."""
    definitions: Dict[str, Dict[str, Any]]

    @classmethod
    def from_csv(cls, path: str | Path) -> "PlatformCatalog":
        path=Path(path); rows={}
        with path.open("r",encoding="utf-8-sig",newline="") as f:
            for row in csv.DictReader(f):
                pid=str(row.get("platform_id","")).strip()
                if not pid: continue
                if pid in rows: raise ValueError(f"Duplicate platform_id {pid!r} in {path}")
                rows[pid]={"platform_id":pid,"platform_name":row.get("name",pid),
                           "platform_category":row.get("category","VEHICLE"),
                           "mobility_class":row.get("mobility_class",""),
                           "protection_class":row.get("protection_class",""),
                           "crew":_int_cell(row.get("crew"),0),"passengers":_int_cell(row.get("passengers"),0),
                           "tags":_json_cell(row.get("tags_json"),[])}
        return cls(rows)

    def resolve(self, platform_id: str) -> Dict[str, Any]:
        try:return dict(self.definitions[platform_id])
        except KeyError as exc:raise KeyError(f"Unknown platform_id {platform_id!r}") from exc
