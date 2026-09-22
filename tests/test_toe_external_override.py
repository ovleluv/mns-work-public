
from mnsim.scenario import load_scenario

def run():
    sim=load_scenario("scenarios/demo.json")
    inf=sim.units["B-INF-2"]
    assert "designated_marksmen" in inf.elements
    assert inf.elements["designated_marksmen"].count==3
    assert inf.elements["at_specialist"].count==2
    assert inf.unit_type.metadata["mobility_class"]=="FOOT"
    print("external TO&E template test: PASS")

if __name__=="__main__": run()
