
from mnsim.scenario import load_scenario

def run():
    sim=load_scenario("scenarios/demo.json")
    arty=sim.units["B-ART-1"]
    ir=sim.indirect_fire
    arty.metadata["artillery_fire_profile"]="CONCENTRATED"
    n,p=ir._fire_pattern(arty)
    tight=[ir._pattern_offset(p,i,5) for i in range(5)]
    arty.metadata["artillery_fire_profile"]="AREA"
    n2,p2=ir._fire_pattern(arty)
    wide=[ir._pattern_offset(p2,i,5) for i in range(5)]
    tight_r=max((x*x+y*y)**0.5 for x,y in tight)
    wide_r=max((x*x+y*y)**0.5 for x,y in wide)
    assert n=="CONCENTRATED" and n2=="AREA"
    assert wide_r > tight_r * 3
    assert sim.units["B-ART-1"].elements["towed_guns"].count==5
    print("artillery doctrine pattern test: PASS")

if __name__=="__main__": run()
