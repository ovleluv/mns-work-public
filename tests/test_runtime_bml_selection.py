import json
from pathlib import Path
from mnsim.scenario import load_scenario


def _write_fixture(tmp_path: Path):
    (tmp_path / "terrain.json").write_text("{}")
    toe={"unit_types":{
        "INF_PLT":{"branch":"INFANTRY","max_speed_mps":1.0,"detection_range_m":500.0,"elements":[]}
    }}
    (tmp_path / "toe.json").write_text(json.dumps(toe))
    scenario={
        "seed":1,"world":{"width_m":1000,"height_m":1000},"objectives":{},
        "terrain_file":"terrain.json","unit_types_file":"toe.json",
        "bml_files":{"BLUE":"legacy_blue.json"},
        "units":[
            {"id":"B1","name":"B1","side":"BLUE","echelon":"PLT","type":"INF_PLT","pos":[100,100],"orders":[{"id":"hold","kind":"HOLD","params":{"duration_s":999}}]},
            {"id":"R1","name":"R1","side":"RED","echelon":"PLT","type":"INF_PLT","pos":[800,800],"orders":[]}
        ]
    }
    (tmp_path / "scenario.json").write_text(json.dumps(scenario))
    legacy={"side":"BLUE","missions":[{"unit":"B1","task":"MOVE_TO","destination":[200,200]}]}
    runtime={"side":"BLUE","missions":[{"unit":"B1","task":"MOVE_TO","destination":[300,300]}]}
    (tmp_path / "legacy_blue.json").write_text(json.dumps(legacy))
    (tmp_path / "runtime_blue.json").write_text(json.dumps(runtime))
    return tmp_path / "scenario.json", tmp_path / "runtime_blue.json"


def test_explicit_empty_runtime_selection_disables_embedded_bml(tmp_path):
    scenario,_=_write_fixture(tmp_path)
    sim=load_scenario(str(scenario), bml_files={})
    assert sim.bml_files == {}
    assert sim.units["B1"].order_queue[0].kind == "HOLD"


def test_runtime_bml_selection_overrides_embedded_reference(tmp_path):
    scenario,runtime=_write_fixture(tmp_path)
    sim=load_scenario(str(scenario), bml_files={"BLUE":str(runtime)})
    assert Path(sim.bml_files["BLUE"]) == runtime.resolve()
    assert len(sim.units["B1"].order_queue) == 1
    assert sim.units["B1"].order_queue[0].kind == "MOVE"
    assert sim.units["B1"].order_queue[0].params["destination"] == [300,300]


def test_legacy_loader_still_honors_embedded_bml_when_no_override_given(tmp_path):
    scenario,_=_write_fixture(tmp_path)
    sim=load_scenario(str(scenario))
    assert Path(sim.bml_files["BLUE"]).name == "legacy_blue.json"
    assert sim.units["B1"].order_queue[0].params["destination"] == [200,200]


def test_runtime_relative_bml_path_is_cwd_relative(tmp_path, monkeypatch):
    scenario,runtime=_write_fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    sim=load_scenario("scenario.json", bml_files={"BLUE":"runtime_blue.json"})
    assert Path(sim.bml_files["BLUE"]) == runtime.resolve()
    assert sim.units["B1"].order_queue[0].params["destination"] == [300,300]
