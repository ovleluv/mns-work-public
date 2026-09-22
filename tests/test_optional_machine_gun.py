import json
from pathlib import Path
from mnsim.scenario import load_scenario

ROOT=Path(__file__).resolve().parents[1]

def test_inf_template_has_optional_machine_gun_section_off_by_default():
    raw=json.loads((ROOT/"config/toe_templates.json").read_text())
    inf=raw["unit_types"]["INF_PLT"]
    mg=next(e for e in inf["elements"] if e["id"]=="machine_gun_section")
    assert mg["count"]==0
    assert mg["metadata"]["optional_attachment"]["present_count"]==4
    assert mg["weapons"][0]["weapon_id"]=="GPMG_SECTION_GENERIC"
    assert mg["weapons"][0]["slot"]=="MACHINE_GUN"

def test_same_inf_type_can_have_or_not_have_machine_gun_section():
    sim=load_scenario(str(ROOT/"scenarios/demo.json"))
    with_mg=sim.units["B-INF-2"]
    without_mg=sim.units["B-INF-3"]
    assert with_mg.elements["machine_gun_section"].count==4
    assert without_mg.elements["machine_gun_section"].count==0
    assert any(w.metadata.get("ui_range_label")=="MG" for _,w in with_mg.operational_weapons())
    assert not any(w.metadata.get("ui_range_label")=="MG" for _,w in without_mg.operational_weapons())

def test_editor_optional_attachment_is_metadata_driven():
    text=(ROOT/"editor.py").read_text()
    assert "optional_attachment" in text
    assert "toggle_optional_attachment" in text
    assert "machine_gun_section" not in text
