import json
from pathlib import Path

from mnsim.scenario import load_scenario


def test_legacy_stale_standard_resource_paths_fall_back_to_current_project(tmp_path):
    root = Path(__file__).resolve().parents[1]
    terrain = tmp_path / 'oldmap_terrain.json'
    terrain.write_text(json.dumps({'roads': [], 'rivers': [], 'bridges': [], 'areas': []}), encoding='utf-8')
    scenario = {
        'seed': 7,
        'world': {'width_m': 500.0, 'height_m': 500.0},
        'objectives': {},
        'units': [
            {'id': 'B-INF-1', 'side': 'BLUE', 'echelon': 'PLT', 'type': 'INF_PLT', 'pos': [100, 100], 'orders': []},
            {'id': 'R-TANK-1', 'side': 'RED', 'echelon': 'IND', 'type': 'US_M1A2_ABRAMS_IND', 'pos': [300, 100], 'orders': []},
        ],
        'aggregations': [],
        'config_file': '../mns_coa_prototype_vOLD/config/defaults.json',
        'unit_types_file': '../mns_coa_prototype_vOLD/config/toe_templates.json',
        'artillery_doctrine_file': '../mns_coa_prototype_vOLD/config/artillery_doctrine.json',
        'targeting_doctrine_file': '../mns_coa_prototype_vOLD/config/targeting_doctrine.json',
        'terrain_file': terrain.name,
    }
    p = tmp_path / 'oldmap.json'
    p.write_text(json.dumps(scenario), encoding='utf-8')
    sim = load_scenario(str(p), bml_files={})
    assert sim.units['B-INF-1'].branch == 'INFANTRY'
    assert sim.units['R-TANK-1'].branch == 'ARMOR'


def test_portable_scenario_can_omit_standard_resource_paths(tmp_path):
    terrain = tmp_path / 'portable_terrain.json'
    terrain.write_text(json.dumps({'roads': [], 'rivers': [], 'bridges': [], 'areas': []}), encoding='utf-8')
    scenario = {
        'seed': 7,
        'world': {'width_m': 500.0, 'height_m': 500.0},
        'objectives': {},
        'units': [{'id': 'B-INF-1', 'side': 'BLUE', 'echelon': 'PLT', 'type': 'INF_PLT', 'pos': [100, 100], 'orders': []}],
        'aggregations': [],
        'terrain_file': terrain.name,
    }
    p = tmp_path / 'portable.json'
    p.write_text(json.dumps(scenario), encoding='utf-8')
    sim = load_scenario(str(p), bml_files={})
    assert sim.units['B-INF-1'].branch == 'INFANTRY'
