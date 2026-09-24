from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass(frozen=True)
class DoctrineProfileCatalog:
    """Side-neutral tactical reaction profiles.

    Profiles only parameterize local DoctrineEngine reactions.  They do not encode COA/BML
    missions and they are never selected implicitly from BLUE/RED.  A scenario/unit explicitly
    selects a profile so two formations of the same side/type may use different doctrine.
    """
    profiles: Dict[str, Dict[str, Any]]

    @classmethod
    def from_json(cls, path: str | Path) -> "DoctrineProfileCatalog":
        from .validation import read_json_file
        raw=read_json_file(path)
        profiles=dict(raw.get("profiles", raw))
        return cls({str(k):dict(v) for k,v in profiles.items()})

    def get(self, name: str | None) -> Dict[str, Any]:
        if not name:
            return {}
        if name not in self.profiles:
            raise KeyError(f"unknown doctrine profile: {name}")
        return dict(self.profiles[name])
