import json
from pathlib import Path
import pytest
from mnsim.scenario import load_scenario
from .support import UNITS

@pytest.fixture(params=UNITS)
def mover(request, tmp_path):
    templates=json.loads((Path(__file__).resolve().parents[2]/"config/toe_templates.json").read_text(encoding="utf-8"))["unit_types"]
    echelon=templates[request.param]["metadata"]["echelon"]
    path=tmp_path/"movement.json"
    path.write_text(json.dumps(dict(seed=7, world=dict(width_m=240,height_m=240), units=[dict(id="U",name="Mover",side="BLUE",type=request.param,echelon=echelon,pos=[40,100])])),encoding="utf-8")
    sim=load_scenario(str(path))
    assert sim.units["U"].unit_type.name==request.param, "scenario loader changed requested unit type"
    return sim,sim.units["U"]

@pytest.fixture
def record(request):
    rows=[]
    yield rows
    request.node.user_properties.append(("movement",json.dumps(rows,ensure_ascii=False)))
