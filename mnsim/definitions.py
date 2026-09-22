from __future__ import annotations

"""Data-definition construction and registration boundary.

This module deliberately contains *no simulation policy*.  It converts trusted,
validated-ish scenario/config dictionaries into the existing core model objects.
Keeping this boundary separate lets future frontends, LLM-assisted authoring tools,
or plugins provide new definition syntaxes without changing combat/sensing/doctrine.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Mapping, MutableMapping, Tuple

from .model import FormationElement, UnitType, WeaponModel
from .database import WeaponCatalog, PlatformCatalog

WeaponBuilder = Callable[[Mapping[str, Any]], WeaponModel]
ElementBuilder = Callable[[Mapping[str, Any], "DefinitionRegistry"], FormationElement]
UnitTypeBuilder = Callable[[str, Mapping[str, Any], "DefinitionRegistry"], UnitType]


def canonical_token(value: Any, default: str = "") -> str:
    """Return the legacy canonical form used throughout v42 (uppercase strings)."""
    if value is None:
        value = default
    return str(value).upper()


def build_weapon(raw: Mapping[str, Any]) -> WeaponModel:
    """Build a WeaponModel using exactly the legacy v42 field/default semantics."""
    return WeaponModel(
        name=raw["name"],
        capability=canonical_token(raw.get("capability", "DIRECT_FIRE")),
        range_m=float(raw["range_m"]),
        shots_per_min=float(raw.get("shots_per_min", 1.0)),
        pk=float(raw.get("pk", 0.1)),
        target_tags=[canonical_token(x) for x in raw.get("target_tags", ["PERSONNEL"])],
        max_effect_count=int(raw.get("max_effect_count", 1)),
        ammo_capacity=int(raw.get("ammo", -1)),
        ammo_remaining=int(raw.get("ammo", -1)),
        metadata=dict(raw.get("metadata", {})),
    )


def build_element(raw: Mapping[str, Any], registry: "DefinitionRegistry") -> FormationElement:
    """Build a FormationElement using catalogs for nested weapon/platform definitions."""
    cnt = int(raw["count"])
    md={}
    platform_id=raw.get("platform_id")
    if platform_id is not None:
        if registry.platform_catalog is None:
            raise ValueError(f"platform_id {platform_id!r} requires a platform database")
        md.update(registry.platform_catalog.resolve(str(platform_id)))
    md.update(dict(raw.get("metadata", {})))
    return FormationElement(
        eid=raw["id"], name=raw.get("name", raw["id"]),
        category=canonical_token(raw.get("category", "PERSONNEL")),
        role=canonical_token(raw.get("role", "RIFLE")), count=cnt,
        initial_count=int(raw.get("initial_count", cnt)), combat_value=float(raw.get("combat_value", 1.0)),
        weapons=[registry.create_weapon(w) for w in raw.get("weapons", [])],
        min_operators=int(raw.get("min_operators", 1)), metadata=md,
        item_states=list(raw.get("item_states", [])),
    )


def build_unit_type(name: str, raw: Mapping[str, Any], registry: "DefinitionRegistry") -> UnitType:
    """Build a UnitType with the same defaults used by the legacy scenario loader."""
    return UnitType(
        name=name,
        branch=canonical_token(raw.get("branch", "INFANTRY")),
        max_speed_mps=float(raw["max_speed_mps"]),
        detection_range_m=float(raw["detection_range_m"]),
        elements=[registry.create_element(e) for e in raw.get("elements", [])],
        metadata=dict(raw.get("metadata", {})),
    )


@dataclass
class DefinitionRegistry:
    """Registry/factory seam for data-driven model definitions.

    v42 behavior uses one default builder for each definition kind.  The registry exists
    so future capability/plugin work can register additional *definition formats* or
    construction adapters without teaching scenario.py about every new system type.

    Registration changes construction only when a caller explicitly selects a non-default
    builder key; existing scenario files therefore retain identical semantics.
    """

    weapon_builders: MutableMapping[str, WeaponBuilder] = field(default_factory=dict)
    weapon_catalog: WeaponCatalog | None = None
    platform_catalog: PlatformCatalog | None = None
    element_builders: MutableMapping[str, ElementBuilder] = field(default_factory=dict)
    unit_type_builders: MutableMapping[str, UnitTypeBuilder] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.weapon_builders.setdefault("DEFAULT", build_weapon)
        self.element_builders.setdefault("DEFAULT", build_element)
        self.unit_type_builders.setdefault("DEFAULT", build_unit_type)

    @staticmethod
    def _key(value: Any) -> str:
        return canonical_token(value, "DEFAULT") or "DEFAULT"

    def register_weapon_builder(self, key: str, builder: WeaponBuilder) -> None:
        self.weapon_builders[self._key(key)] = builder

    def register_element_builder(self, key: str, builder: ElementBuilder) -> None:
        self.element_builders[self._key(key)] = builder

    def register_unit_type_builder(self, key: str, builder: UnitTypeBuilder) -> None:
        self.unit_type_builders[self._key(key)] = builder

    def create_weapon(self, raw: Mapping[str, Any]) -> WeaponModel:
        # New data-driven path: a template/loadout references a stable weapon_id.
        # Inline legacy definitions remain supported for old scenarios/tests.
        effective = dict(raw)
        weapon_id = effective.get("weapon_id")
        if weapon_id is not None:
            if self.weapon_catalog is None:
                raise ValueError(f"weapon_id {weapon_id!r} requires a weapon database")
            base = self.weapon_catalog.resolve(str(weapon_id))
            # Explicit fields on the reference are allowed as local overrides. Structural
            # fields such as slot are retained as metadata, not interpreted as weapon physics.
            overrides = {k: v for k, v in effective.items() if k not in {"weapon_id", "slot", "metadata"}}
            base.update(overrides)
            # Reference-local metadata augments rather than replaces catalog metadata.
            # This is used for editor/runtime composition fields such as system_count and
            # operators_per_system while retaining weapon physics from weapons.csv.
            md = dict(base.get("metadata", {}))
            md.update(dict(effective.get("metadata", {})))
            md["weapon_id"] = str(weapon_id)
            if effective.get("slot") is not None:
                md["loadout_slot"] = str(effective["slot"])
            base["metadata"] = md
            effective = base
        key = self._key(effective.get("definition_kind", "DEFAULT"))
        try:
            return self.weapon_builders[key](effective)
        except KeyError as exc:
            raise ValueError(f"Unknown weapon definition_kind {key!r}") from exc

    def create_element(self, raw: Mapping[str, Any]) -> FormationElement:
        key = self._key(raw.get("definition_kind", "DEFAULT"))
        try:
            return self.element_builders[key](raw, self)
        except KeyError as exc:
            raise ValueError(f"Unknown element definition_kind {key!r}") from exc

    def create_unit_type(self, name: str, raw: Mapping[str, Any]) -> UnitType:
        key = self._key(raw.get("definition_kind", "DEFAULT"))
        try:
            return self.unit_type_builders[key](name, raw, self)
        except KeyError as exc:
            raise ValueError(f"Unknown unit-type definition_kind {key!r}") from exc


def default_definition_registry(weapon_catalog: WeaponCatalog | None = None, platform_catalog: PlatformCatalog | None = None) -> DefinitionRegistry:
    """Create an isolated registry so one simulation load cannot mutate another."""
    return DefinitionRegistry(weapon_catalog=weapon_catalog, platform_catalog=platform_catalog)
