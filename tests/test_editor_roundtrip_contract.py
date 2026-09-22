import json
from pathlib import Path
from mnsim.scenario import load_scenario

def test_editor_contract_files_and_demo_load():
    root=Path(__file__).resolve().parents[1]
    assert (root/'editor.py').exists()
    sim=load_scenario(str(root/'scenarios/demo.json'))
    assert sim.units and sim.terrain.rivers and sim.terrain.bridges
