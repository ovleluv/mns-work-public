from mnsim.catalog import UnitTypeCatalog
from mnsim.definitions import DefinitionRegistry, default_definition_registry


def _raw_weapon():
    return {
        "name": "TEST-RIFLE",
        "range_m": 300,
        "shots_per_min": 7,
        "pk": 0.11,
        "ammo": 5,
        "target_tags": ["personnel"],
    }


def test_default_definition_registry_preserves_legacy_defaults():
    reg = default_definition_registry()
    w = reg.create_weapon(_raw_weapon())
    assert w.capability == "DIRECT_FIRE"
    assert w.target_tags == ["PERSONNEL"]
    assert w.ammo_capacity == 5 and w.ammo_remaining == 5

    e = reg.create_element({
        "id": "rifle",
        "count": 8,
        "weapons": [_raw_weapon()],
    })
    assert e.category == "PERSONNEL"
    assert e.role == "RIFLE"
    assert e.count == e.initial_count == 8
    assert e.weapons[0].name == "TEST-RIFLE"


def test_registry_extension_is_opt_in_and_does_not_change_default_path():
    reg = DefinitionRegistry()
    called = []

    def special(raw):
        called.append(raw["name"])
        w = reg.weapon_builders["DEFAULT"](raw)
        w.metadata["builder"] = "SPECIAL"
        return w

    reg.register_weapon_builder("SPECIAL", special)
    normal = reg.create_weapon(_raw_weapon())
    special_raw = dict(_raw_weapon(), definition_kind="SPECIAL", name="SPECIAL-RIFLE")
    extended = reg.create_weapon(special_raw)
    assert "builder" not in normal.metadata
    assert extended.metadata["builder"] == "SPECIAL"
    assert called == ["SPECIAL-RIFLE"]


def test_unit_type_catalog_retains_legacy_echelon_resolution():
    raw = {
        "INF_PLT": {
            "branch": "INFANTRY", "max_speed_mps": 1.0, "detection_range_m": 800,
            "metadata": {"echelon": "PLT", "formation_family": "INF"}, "elements": []
        },
        "INF_BN": {
            "branch": "INFANTRY", "max_speed_mps": 1.0, "detection_range_m": 800,
            "metadata": {"echelon": "BN", "formation_family": "INF"}, "elements": []
        },
    }
    catalog = UnitTypeCatalog.from_mapping(raw)
    name, typ, upgraded = catalog.resolve_formation_type("INF_PLT", "BN")
    assert (name, typ.name, upgraded) == ("INF_BN", "INF_BN", True)
