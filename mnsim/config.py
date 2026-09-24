
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict

def deep_update(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    """Recursive dict merge used by file -> scenario -> runtime configuration layers."""
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            deep_update(dst[key], value)
        else:
            dst[key] = value
    return dst

def load_json_config(path: Path) -> Dict[str, Any]:
    """Load a JSON document, rejecting NaN/Infinity and oversize files."""
    from .validation import read_json_file
    return read_json_file(path)
