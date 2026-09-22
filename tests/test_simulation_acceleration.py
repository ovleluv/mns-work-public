from mnsim.simulation import Simulation


def test_advance_realtime_reaches_32x_elapsed_time():
    sim = Simulation(seed=1)
    sim.speed = 32.0
    sim.advance_realtime(0.05)
    assert abs(sim.time - 1.6) < 1e-9


def test_high_speed_realtime_uses_bounded_substeps():
    sim = Simulation(seed=1)
    sim.speed = 32.0
    calls = []
    original_tick = sim.tick

    def recording_tick(dt):
        calls.append(dt * sim.speed)
        original_tick(dt)

    sim.tick = recording_tick
    sim.advance_realtime(0.05, max_sim_step_s=0.25)
    assert len(calls) == 7
    assert max(calls) <= 0.25 + 1e-9
    assert abs(sum(calls) - 1.6) < 1e-9


def test_advance_realtime_respects_pause():
    sim = Simulation(seed=1)
    sim.speed = 32.0
    sim.paused = True
    sim.advance_realtime(1.0)
    assert sim.time == 0.0
