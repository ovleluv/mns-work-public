from __future__ import annotations

"""TO&E/unit-type catalog utilities.

The catalog owns template lookup and legacy echelon-resolution policy.  It intentionally
knows nothing about Simulation, combat, doctrine, UI, or BML.
"""

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Tuple

from .definitions import DefinitionRegistry, default_definition_registry
from .model import UnitType


@dataclass
class UnitTypeCatalog:
    types: Dict[str, UnitType]

    @classmethod
    def from_mapping(
        cls,
        raw_types: Mapping[str, Mapping[str, Any]],
        registry: DefinitionRegistry | None = None,
    ) -> "UnitTypeCatalog":
        reg = registry or default_definition_registry()
        return cls({name: reg.create_unit_type(name, raw) for name, raw in raw_types.items()})

    def resolve_formation_type(self, requested_name: str, echelon: str) -> Tuple[str, UnitType, bool]:
        """Resolve legacy echelon/template mismatches with unchanged v42 semantics."""
        if requested_name not in self.types:
            raise KeyError(f"Unknown unit type {requested_name!r}")
        requested = self.types[requested_name]
        ech = str(echelon or "PLT").upper()
        template_ech = str(requested.metadata.get("echelon", "")).upper()
        if not template_ech or template_ech == ech:
            return requested_name, requested, False
        family = str(requested.metadata.get("formation_family", ""))
        for name, candidate in self.types.items():
            md = candidate.metadata
            if (
                str(md.get("echelon", "")).upper() == ech
                and family
                and str(md.get("formation_family", "")) == family
            ):
                return name, candidate, True
        raise ValueError(
            f"Unit type/echelon mismatch: {requested_name} is {template_ech}, "
            f"but scenario requests {ech}; no matching TO&E template exists"
        )
