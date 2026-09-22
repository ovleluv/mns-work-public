from mnsim.symbology import echelon_amplifier


def test_nato_echelon_amplifiers():
    assert echelon_amplifier("IND") == ""
    assert echelon_amplifier("TEAM") == "Ø"
    assert echelon_amplifier("SQD") == "•"
    assert echelon_amplifier("SEC") == "••"
    assert echelon_amplifier("PLT") == "•••"
    assert echelon_amplifier("COY") == "I"
    assert echelon_amplifier("BN") == "II"
    assert echelon_amplifier("REG") == "III"
    assert echelon_amplifier("BDE") == "X"
    assert echelon_amplifier("DIV") == "XX"
    assert echelon_amplifier("CORPS") == "XXX"
    assert echelon_amplifier("ARMY") == "XXXX"
