from mnsim.scenario import load_scenario


def test_salvo_provides_multiple_capped_detection_opportunities():
    sim = load_scenario("scenarios/demo.json")
    md = sim.units["B-ART-1"].elements["cb_radar"].metadata
    p1, n1 = sim._counter_battery_salvo_probability(0.40, 1, md)
    p5, n5 = sim._counter_battery_salvo_probability(0.40, 5, md)
    assert n1 == 1
    assert n5 == md["max_salvo_detection_opportunities"] == 3
    assert abs(p1 - 0.40) < 1e-9
    assert p5 > p1
    assert abs(p5 - (1.0 - 0.60 ** 3)) < 1e-9


def test_point_of_origin_solution_survives_shooter_disable_during_processing():
    sim = load_scenario("scenarios/demo.json")
    radar_unit = sim.units["B-ART-1"]
    shooter = sim.units["R-ART-1"]
    radar = radar_unit.elements["cb_radar"]
    radar.metadata["detect_p_near"] = 1.0
    radar.metadata["detect_p_edge"] = 1.0
    radar.metadata["processing_delay_min_s"] = 2.0
    radar.metadata["processing_delay_max_s"] = 2.0

    sim.notify_indirect_fire_launch(shooter, "test", "FIRE_SUPPORT", projectile_count=5)
    assert any(x["kind"] == "CB_RADAR_DETECTION_PENDING" and x.get("source") == shooter.uid for x in sim.logs)

    # The launcher may be disabled/destroyed after firing, but the already observed ballistic
    # trajectory still permits the radar to finish its point-of-origin solution.
    for e in shooter.elements.values():
        e.count = 0
    sim.time = 2.1
    for ev in list(sim.events.pop_due(sim.time)):
        sim._handle_event(ev.kind, ev.payload)

    assert any(x["kind"] == "CB_RADAR_DETECT" and x.get("source") == shooter.uid for x in sim.logs)
    assert shooter.uid in radar_unit.local_tracks


def test_indirect_fire_passes_actual_salvo_size_to_radar():
    sim = load_scenario("scenarios/demo.json")
    shooter = sim.units["R-ART-1"]
    target = sim.units["B-INF-2"]
    radar = sim.units["B-ART-1"].elements["cb_radar"]
    radar.metadata["detect_p_near"] = 1.0
    radar.metadata["detect_p_edge"] = 1.0
    radar.metadata["processing_delay_min_s"] = 0.0
    radar.metadata["processing_delay_max_s"] = 0.0

    # Bypass fire-control delays and launch the already-prepared mission directly.
    gun_el, weapon = next((e, w) for e, w in shooter.operational_weapons() if w.capability == "INDIRECT_FIRE")
    sim.indirect_fire.launch_prepared_mission(
        shooter, gun_el, weapon, target.uid, target.pos, 10.0, 0.9,
        sim.time, "FIRE_SUPPORT", sim.time
    )
    launch = next(x for x in reversed(sim.logs) if x["kind"] == "INDIRECT_LAUNCH")
    assert launch["projectile_count"] >= 5
    pending = next(x for x in reversed(sim.logs) if x["kind"] == "CB_RADAR_DETECTION_PENDING")
    assert pending["projectile_count"] == launch["projectile_count"]
    assert pending["trajectory_opportunities"] == 3
