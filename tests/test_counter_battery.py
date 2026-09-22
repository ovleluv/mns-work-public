from mnsim.scenario import load_scenario


def test_radar_disable_stops_detection():
    sim = load_scenario("scenarios/demo.json")
    blue = sim.units["B-ART-1"]
    red = sim.units["R-ART-1"]

    # A disabled radar must not even create a pending fire-origin solution.
    blue.elements["cb_radar"].count = 0
    before = len([r for r in sim.logs if r["kind"] == "CB_RADAR_DETECTION_PENDING" and r.get("radar") == blue.uid])
    for _ in range(20):
        sim._counter_battery_observe(red, "test indirect fire")
    after = len([r for r in sim.logs if r["kind"] == "CB_RADAR_DETECTION_PENDING" and r.get("radar") == blue.uid])
    assert before == after


def test_demo_generates_counter_battery_track():
    sim = load_scenario("scenarios/demo.json")
    # Architecture regression: make detection deterministic; probability behavior is tested elsewhere.
    for uid in ("B-ART-1","R-ART-1"):
        radar=sim.units[uid].elements["cb_radar"]
        radar.metadata["detect_p_near"]=1.0
        radar.metadata["detect_p_edge"]=1.0
    for _ in range(6000):
        sim.tick(0.05)
    assert any(r["kind"] == "CB_RADAR_DETECT" for r in sim.logs)
    assert any(r["kind"] == "INDIRECT_FIRE" and r.get("mode") == "COUNTER_BATTERY" for r in sim.logs)


if __name__ == "__main__":
    test_radar_disable_stops_detection()
    test_demo_generates_counter_battery_track()
    print("counter-battery tests: PASS")
