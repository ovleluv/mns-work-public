import json
from pathlib import Path


def test_vehicle_templates_exist_for_editor_echelons():
    root = Path(__file__).resolve().parents[1]
    defs = json.loads((root/'config'/'toe_templates.json').read_text())['unit_types']
    expected_ind = {
        'US_M1A2_ABRAMS_IND','ROK_K2_TANK_IND','US_M2_BRADLEY_IND',
        'BMP_IFV_IND','ROK_K200_APC_IND','US_HMMWV_IND'
    }
    assert expected_ind <= {k for k,v in defs.items() if v.get('metadata',{}).get('echelon') == 'IND'}
    expected_plt = {'TANK_PLT','US_MECH_INF_PLT_BRADLEY','BMP_MECH_INF_PLT','ROK_MECH_INF_PLT_K200','US_MOT_INF_PLT_HMMWV'}
    assert expected_plt <= {k for k,v in defs.items() if v.get('metadata',{}).get('echelon') == 'PLT'}
