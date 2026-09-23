from mnsim.scenario import load_scenario
from mnsim.simulation import Simulation


def test_advance_realtime_reaches_32x_elapsed_time():
    sim = Simulation(seed=1)
    sim.speed = 32.0
    sim.advance_realtime(0.0625)
    assert abs(sim.time - 2.0) < 1e-9


def test_high_speed_realtime_uses_fixed_substeps_and_carries_remainder():
    sim = Simulation(seed=1)
    sim.speed = 32.0
    calls = []
    original_step = sim.step

    def recording_step(dt):
        calls.append(dt)
        original_step(dt)

    sim.step = recording_step
    sim.advance_realtime(0.05, max_sim_step_s=0.25)
    # 1.6 s of simulation time is six whole 0.25 s steps; the 0.1 s remainder is carried.
    assert calls == [0.25] * 6
    sim.advance_realtime(0.05, max_sim_step_s=0.25)
    assert len(calls) == 12  # 0.1 carried + 1.6 = 1.7 s -> six more steps
    assert all(abs(c - 0.25) < 1e-12 for c in calls)
    assert abs(sim.time - 0.25 * len(calls)) < 1e-9


def test_advance_realtime_respects_pause():
    sim = Simulation(seed=1)
    sim.speed = 32.0
    sim.paused = True
    sim.advance_realtime(1.0)
    assert sim.time == 0.0


def test_realtime_result_is_independent_of_frame_rate_and_speed():
    """Same simulated duration must give identical results for any frame pacing or speed."""
    def run(speed, frame_s, total_sim_s=120.0):
        sim = load_scenario("scenarios/demo.json")
        sim.speed = speed
        while sim.time < total_sim_s - 1e-9:
            sim.advance_realtime(frame_s)
        return sim.time, [(e["t"], e["kind"]) for e in sim.logs]

    a = run(1.0, 1 / 60)
    b = run(32.0, 0.05)
    c = run(8.0, 0.2)
    n = min(len(a[1]), len(b[1]), len(c[1]))
    assert n > 0
    assert a[1][:n] == b[1][:n] == c[1][:n]
