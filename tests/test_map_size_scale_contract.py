import json
from pathlib import Path
from mnsim.scenario import load_scenario

ROOT=Path(__file__).resolve().parents[1]

def test_loader_preserves_non_square_custom_world_size(tmp_path):
    raw=json.loads((ROOT/"scenarios/demo.json").read_text())
    raw["world"]={"width_m":7500,"height_m":3200}
    for key, ref in list(raw.items()):
        if key.endswith("_file") and ref:
            raw[key]=str((ROOT/"scenarios"/ref).resolve())
    tmp=tmp_path/"world_size.json"
    tmp.write_text(json.dumps(raw),encoding="utf-8")
    sim=load_scenario(str(tmp))
    assert sim.world["width_m"]==7500
    assert sim.world["height_m"]==3200

def test_metric_grid_and_scale_ui_contract():
    editor=(ROOT/"editor.py").read_text(encoding="utf-8")
    main=(ROOT/"main.py").read_text(encoding="utf-8")
    assert "set_map_size_dialog" in editor
    assert "Grid {format_metric(step)}" in editor
    assert "_draw_scale_bar" in main
    assert "_grid_interval_for_scale" in main
