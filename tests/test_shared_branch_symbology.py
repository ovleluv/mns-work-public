from mnsim.symbology import branch_symbol_kind


def test_new_branch_symbol_kinds_are_shared_and_explicit():
    assert branch_symbol_kind('INFANTRY') == 'INFANTRY'
    assert branch_symbol_kind('MOTORIZED_INFANTRY') == 'MOTORIZED_INFANTRY'
    assert branch_symbol_kind('MECH_INFANTRY') == 'MECH_INFANTRY'
    assert branch_symbol_kind('ARMOR') == 'ARMOR'
    assert branch_symbol_kind('ARTILLERY') == 'ARTILLERY'
    assert branch_symbol_kind('SOMETHING_NEW') == 'GENERIC'
