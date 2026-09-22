from pathlib import Path
from mnsim.database import WeaponCatalog


def _weapon_metadata(name: str):
    db=WeaponCatalog.from_csv(Path(__file__).parents[1]/"database"/"weapons.csv")
    for raw in db.definitions.values():
        if raw.get("name")==name:
            return raw.get("metadata", {})
    return None


def _weapon_metadata_by_id(weapon_id: str):
    db=WeaponCatalog.from_csv(Path(__file__).parents[1]/"database"/"weapons.csv")
    return db.definitions[weapon_id].get("metadata", {})


def test_generic_armor_mobility_kill_probabilities_are_conservative():
    gun=_weapon_metadata("tank main gun anti-armor")
    at=_weapon_metadata_by_id("ATGM_GENERIC")
    arty=_weapon_metadata("abstract towed indirect fire")
    assert gun["p_mobility_kill_on_armor_hit"] == 0.01
    assert at["p_mobility_kill_on_armor_hit"] == 0.03
    assert arty["p_mobility_near_armor"] == 0.01
    assert arty["p_mobility_fragment_armor"] == 0.001
    assert arty["p_mobility_fragment_armor"] < arty["p_mobility_near_armor"]


def test_direct_armor_weapons_do_not_convert_reduced_mobility_probability_into_disabled():
    gun=_weapon_metadata("tank main gun anti-armor")
    at=_weapon_metadata_by_id("ATGM_GENERIC")
    assert gun["p_disabled_on_armor_hit"] == 0.05
    assert at["p_disabled_on_armor_hit"] == 0.06
    assert gun["p_mobility_kill_on_armor_hit"] + gun["p_disabled_on_armor_hit"] <= 0.061
    assert at["p_mobility_kill_on_armor_hit"] + at["p_disabled_on_armor_hit"] <= 0.091
