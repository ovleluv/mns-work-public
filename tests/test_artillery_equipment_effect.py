
from mnsim.scenario import load_scenario

def run():
    sim=load_scenario("scenarios/demo.json")
    red_arty=sim.units["R-ART-1"]
    guns=red_arty.elements["towed_guns"]
    weapon=next(w for w in sim.units["B-ART-1"].elements["towed_guns"].weapons if w.capability=="INDIRECT_FIRE")

    # Direct/very-near artillery impact must have a meaningful path to disable/destroy soft guns.
    weapon.metadata["p_destroy_direct_soft"]=1.0
    effect,geom=sim.indirect_fire._equipment_blast_effect(guns,0.0,weapon)
    assert geom=="DIRECT" and effect=="DESTROYED"

    weapon.metadata["p_disable_near_soft"]=1.0
    effect,geom=sim.indirect_fire._equipment_blast_effect(guns,10.0,weapon)
    assert geom=="NEAR" and effect=="DISABLED"
    print("artillery equipment blast-effect test: PASS")

if __name__=="__main__": run()
