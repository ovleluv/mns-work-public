"""Shared military symbology helpers.

Echelon amplifiers follow the NATO/MIL-STD-2525 convention used by the
simulator UI.  This module is intentionally GUI-framework neutral so the
same mapping can be reused by the current pygame front ends and a future Qt
front end.
"""
from __future__ import annotations

# MIL-STD-2525 / APP-6 style echelon amplifier mapping.
# IND is a simulator-specific individual entity and therefore has no echelon
# amplifier. TEAM/CREW is retained for standards-compliant data imported from
# external scenarios even though the editor does not currently expose it.
_ECHELON_AMPLIFIERS = {
    "IND": "",
    "TEAM": "Ø",
    "CREW": "Ø",
    "SQD": "•",
    "SQUAD": "•",
    "SEC": "••",
    "SECTION": "••",
    "PLT": "•••",
    "PLATOON": "•••",
    "DET": "•••",
    "DETACHMENT": "•••",
    "COY": "I",
    "COMPANY": "I",
    "BTRY": "I",
    "BATTERY": "I",
    "TRP": "I",
    "TROOP": "I",
    "BN": "II",
    "BATTALION": "II",
    "SQDN": "II",
    "SQUADRON": "II",
    "REG": "III",
    "REGIMENT": "III",
    "GROUP": "III",
    "BDE": "X",
    "BRIGADE": "X",
    "DIV": "XX",
    "DIVISION": "XX",
    "CORPS": "XXX",
    "MEF": "XXX",
    "ARMY": "XXXX",
    "ARMY_GROUP": "XXXXX",
    "FRONT": "XXXXX",
    "REGION": "XXXXXX",
}


def echelon_amplifier(echelon: str | None) -> str:
    """Return the NATO/MIL-STD-style graphic echelon amplifier."""
    return _ECHELON_AMPLIFIERS.get(str(echelon or "").strip().upper(), "")


_BRANCH_SYMBOL_KINDS = {
    "INFANTRY": "INFANTRY",
    "MOTORIZED_INFANTRY": "MOTORIZED_INFANTRY",
    "MECH_INFANTRY": "MECH_INFANTRY",
    "ARMOR": "ARMOR",
    "ARTILLERY": "ARTILLERY",
}

def branch_symbol_kind(branch: str | None) -> str:
    """Return the shared tactical symbol interior kind for a unit/track branch.

    Keeping this mapping GUI-neutral prevents the editor and runtime renderer from
    silently diverging when new branches are added. Unknown branches intentionally
    return ``GENERIC`` so affiliation/echelon framing still renders.
    """
    return _BRANCH_SYMBOL_KINDS.get(str(branch or "").strip().upper(), "GENERIC")
