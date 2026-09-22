"""Regression: a local Track inside nominal weapon range must not freeze a unit behind dense vegetation."""
from types import SimpleNamespace
from mnsim.combat import CombatResolver

class W:
    capability='DIRECT_FIRE'; range_m=500.0

class Track:
    estimated_pos=(200.0,0.0)

class Terrain:
    def __init__(self, allowed): self.allowed=allowed
    def direct_fire_modifier(self,a,b): return {'allowed':self.allowed,'effect_factor':1.0}

class Shooter:
    pos=(0.0,0.0)
    def operational_weapons(self): return [('E',W())]

class Target: pass

def make(allowed):
    sim=SimpleNamespace(terrain=Terrain(allowed))
    sim._track_for=lambda s,t,m='DIRECT': Track()
    r=CombatResolver(sim)
    r.weapon_can_affect=lambda w,t: True
    return r

def test_direct_fire_requires_current_terrain_lane():
    assert make(False).unit_can_affect(Shooter(),Target(),'DIRECT') is False
    assert make(True).unit_can_affect(Shooter(),Target(),'DIRECT') is True
