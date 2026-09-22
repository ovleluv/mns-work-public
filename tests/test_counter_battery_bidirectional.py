
from mnsim.scenario import load_scenario

def run():
    sim=load_scenario("scenarios/demo.json")
    b=sim.units["B-ART-1"]; r=sim.units["R-ART-1"]

    # Deterministic architecture test: both radars must be able to detect the opposite launch.
    for u in (b,r):
        radar=u.elements["cb_radar"]
        radar.metadata["detect_p_near"]=1.0
        radar.metadata["detect_p_edge"]=1.0
        radar.metadata["processing_delay_min_s"]=0.0
        radar.metadata["processing_delay_max_s"]=0.0

    sim.notify_indirect_fire_launch(b,"test-blue","FIRE_SUPPORT")
    sim.notify_indirect_fire_launch(r,"test-red","FIRE_SUPPORT")
    for ev in list(sim.events.pop_due(sim.time)):
        sim._handle_event(ev.kind,ev.payload)

    assert any(x["kind"]=="CB_RADAR_DETECT" and x.get("radar")=="R-ART-1" and x.get("source")=="B-ART-1" for x in sim.logs)
    assert any(x["kind"]=="CB_RADAR_DETECT" and x.get("radar")=="B-ART-1" and x.get("source")=="R-ART-1" for x in sim.logs)
    print("bidirectional counter-battery radar test: PASS")

if __name__=="__main__": run()
