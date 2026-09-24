"""Armor protection classes, artillery bias/precision/TOF, prepared positions, shoot-and-scoot."""
import math
from pathlib import Path

from mnsim.model import UnitState
from mnsim.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[1]


def _sim():
    return load_scenario(str(ROOT / "scenarios" / "demo.json"), bml_files={})


def _weapon(sim, weapon_id):
    from mnsim.database import WeaponCatalog
    from mnsim.definitions import default_definition_registry
    reg = default_definition_registry(WeaponCatalog.from_csv(ROOT / "database" / "weapons.csv"))
    return reg.create_weapon({"weapon_id": weapon_id})


def test_autocannon_is_far_less_lethal_against_heavy_than_light_armor():
    sim = _sim()
    tank = next(u for u in sim.units.values() if u.branch == "ARMOR")
    el = next(e for e in tank.elements.values() if "ARMOR" in e.tags)
    cannon = _weapon(sim, "IFV_CANNON_GENERIC")
    el.metadata["protection_class"] = "HEAVY_ARMOR"
    heavy = sim.damage.armor_vulnerability(el, cannon)
    el.metadata["protection_class"] = "LIGHT_ARMOR"
    light = sim.damage.armor_vulnerability(el, cannon)
    assert heavy < 0.3 < 1.0 < light
    atgm = _weapon(sim, "ATGM_GENERIC")
    el.metadata["protection_class"] = "HEAVY_ARMOR"
    assert sim.damage.armor_vulnerability(el, atgm) == 1.0


def test_artillery_rounds_share_one_mission_bias():
    sim = _sim()
    arty = sim.units["B-ART-1"]
    el, w = next((e, w) for e, w in arty.operational_weapons() if w.capability.upper() == "INDIRECT_FIRE")
    aim = (arty.pos[0] + 2000.0, arty.pos[1])
    sim.indirect_fire.launch_prepared_mission(arty, el, w, "X", aim, 200.0, 0.8, sim.time, "FIRE_SUPPORT", sim.time)
    impacts = [ev.payload["pos"] for ev in sim.events._q if ev.kind == "ARTY_IMPACT_RESOLVE"]
    assert len(impacts) >= 3
    cx = sum(p[0] for p in impacts) / len(impacts); cy = sum(p[1] for p in impacts) / len(impacts)
    spread = max(math.dist(p, (cx, cy)) for p in impacts)
    # Rounds cluster around one (biased) point: spread is the weapon CEP scale, not the 200 m error.
    assert spread < 150.0


def test_time_of_flight_grows_with_range():
    sim = _sim()
    arty = sim.units["B-ART-1"]
    el, w = next((e, w) for e, w in arty.operational_weapons() if w.capability.upper() == "INDIRECT_FIRE")
    def tof(dist):
        sim.events._q.clear()
        sim.indirect_fire.launch_prepared_mission(arty, el, w, "X", (arty.pos[0] + dist, arty.pos[1]), 10.0, 0.9,
                                                  sim.time, "FIRE_SUPPORT", sim.time)
        return min(ev.time for ev in sim.events._q if ev.kind == "ARTY_IMPACT_RESOLVE") - sim.time
    assert tof(2800.0) > tof(1000.0) * 1.8


def test_dug_in_troops_take_fewer_artillery_casualties():
    sim = _sim()
    arty = sim.units["B-ART-1"]
    w = next(w for e, w in arty.operational_weapons() if w.capability.upper() == "INDIRECT_FIRE")
    tgt = sim.units["R-INF-1"]
    from mnsim.formation_geometry import footprint_for
    el = next(e for e in tgt.elements.values() if e.category.upper() == "PERSONNEL" and e.count >= 5)
    def mean_loss(state, dig_since):
        tgt.state = state
        tgt.metadata.pop("_defending_since", None)
        if dig_since is not None:
            tgt.metadata["_defending_since"] = dig_since
        tot = 0
        for _ in range(300):
            loss, _ = sim.indirect_fire._personnel_losses(tgt, el, tgt.pos, w, footprint_for(sim, tgt))
            tot += loss
        return tot / 300
    open_ground = mean_loss(UnitState.MOVING, None)
    dug_in = mean_loss(UnitState.DEFENDING, sim.time - 5000.0)
    assert dug_in < open_ground * 0.45


def test_battery_under_counterfire_displaces_and_pauses_new_missions():
    sim = _sim()
    arty = sim.units["R-ART-1"]
    arty.metadata.update(threat_cue_type="INDIRECT_FIRE", threat_cue_until_t=sim.time + 8.0,
                         threat_cue_heading_deg=180.0)
    start = arty.pos
    assert sim.doctrine._counterfire_displacement(arty, 0.25)
    assert "_scoot_dest" in arty.metadata
    for _ in range(40):
        sim.doctrine._counterfire_displacement(arty, 0.25); sim.time += 0.25
    assert math.dist(start, arty.pos) > 5.0
    assert any(e["kind"] == "ARTILLERY_DISPLACE_COUNTERFIRE" for e in sim.logs)
