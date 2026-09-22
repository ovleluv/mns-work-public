import json
from pathlib import Path

from mnsim.scenario import load_scenario


def test_default_higher_echelon_templates_have_real_scaled_composition():
    root = Path(__file__).resolve().parents[1]
    types = json.loads((root / 'config/toe_templates.json').read_text())['unit_types']

    def totals(name):
        es = types[name]['elements']
        personnel = sum(int(e.get('count', 0)) for e in es if e.get('category') == 'PERSONNEL')
        equipment = sum(int(e.get('count', 0)) for e in es if e.get('category') == 'EQUIPMENT')
        return personnel, equipment, len(es)

    assert totals('INF_COY')[0] > totals('INF_PLT')[0]
    assert totals('INF_BN')[0] > totals('INF_COY')[0]
    assert totals('TANK_COY')[1] > totals('TANK_PLT')[1]
    assert totals('TANK_BN')[1] > totals('TANK_COY')[1]
    # Multiple elements matter because direct-fire cycles are element-scoped.
    assert totals('TANK_COY')[2] > totals('TANK_PLT')[2]
    assert totals('TANK_BN')[2] > totals('TANK_COY')[2]


def test_loader_repairs_legacy_bn_plus_platoon_template(tmp_path):
    root = Path(__file__).resolve().parents[1]
    scenario = {
        'seed': 1,
        'world': {'width_m': 4000, 'height_m': 4000},
        'unit_types_file': str((root / 'config/toe_templates.json').resolve()),
        'units': [{
            'id': 'B-LEGACY-1', 'name': 'B-LEGACY-1', 'side': 'BLUE',
            'echelon': 'BN', 'type': 'INF_PLT', 'pos': [1000, 1000]
        }]
    }
    p = tmp_path / 'legacy.json'
    p.write_text(json.dumps(scenario))
    sim = load_scenario(str(p))
    u = sim.units['B-LEGACY-1']
    assert u.echelon == 'BN'
    assert u.unit_type.name == 'INF_BN'
    assert u.personnel > 200
    assert u.metadata['legacy_type_requested'] == 'INF_PLT'
    assert u.metadata['resolved_type'] == 'INF_BN'
