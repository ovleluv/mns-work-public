from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Dict, Tuple
from .model import Unit, FormationElement

Vec2=Tuple[float,float]

@dataclass(frozen=True)
class FormationFootprint:
    length_m: float
    width_m: float
    orientation_deg: float
    pattern: str = "ELLIPSE"
    density_model: str = "CENTER_WEIGHTED"

    @property
    def semi_major(self): return max(1.0,self.length_m*0.5)
    @property
    def semi_minor(self): return max(1.0,self.width_m*0.5)


def _branch_profile(sim, unit:Unit)->Dict:
    cfg=sim.combat_config.get("formation_footprints",{})
    base=dict(cfg.get("DEFAULT",{}))
    base.update(dict(cfg.get(unit.branch,{})))
    override=unit.metadata.get("formation_footprint",{})
    if isinstance(override,dict): base.update(override)
    return base


def footprint_for(sim, unit:Unit)->FormationFootprint:
    p=_branch_profile(sim,unit)
    posture=str(unit.metadata.get("dispersion_posture",p.get("default_posture","NORMAL"))).upper()
    postures=sim.combat_config.get("formation_posture_modifiers",{})
    pm=dict(postures.get(posture,postures.get("NORMAL",{})))
    lm=float(p.get("length_m",80.0))*float(pm.get("length_factor",1.0))
    wm=float(p.get("width_m",60.0))*float(pm.get("width_factor",1.0))
    # A company/battalion is not hundreds of personnel stacked inside a platoon-sized ellipse.
    # Keep density approximately stable as aggregate echelon grows.  Scenario/unit metadata may
    # override the generic scale, while defaults remain data-driven in combat config.
    echelon_scale_cfg=sim.combat_config.get("formation_echelon_footprint_scale",{})
    echelon_scale=float(unit.metadata.get(
        "formation_echelon_scale", echelon_scale_cfg.get(str(unit.echelon).upper(),1.0)
    ))
    lm*=max(0.25,echelon_scale); wm*=max(0.25,echelon_scale)
    # Losses reduce density first, not footprint. Only severe depletion modestly contracts it.
    strength=max(0.0,min(1.0,unit.strength_ratio))
    contraction=1.0 if strength>=0.5 else 0.82+0.36*strength
    lm*=contraction; wm*=contraction
    orientation=float(unit.metadata.get("formation_orientation_deg",unit.heading_deg))%360.0
    return FormationFootprint(lm,wm,orientation,str(p.get("pattern","ELLIPSE")).upper(),str(p.get("density_model","CENTER_WEIGHTED")).upper())


def world_from_local(center:Vec2, orientation_deg:float, local:Vec2)->Vec2:
    a=math.radians(orientation_deg); c=math.cos(a); s=math.sin(a)
    x,y=local
    return center[0]+x*c-y*s, center[1]+x*s+y*c


def normalized_ellipse_radius(fp:FormationFootprint, world_pos:Vec2, center:Vec2)->float:
    dx=world_pos[0]-center[0]; dy=world_pos[1]-center[1]
    a=math.radians(-fp.orientation_deg); c=math.cos(a); s=math.sin(a)
    lx=dx*c-dy*s; ly=dx*s+dy*c
    return math.sqrt((lx/fp.semi_major)**2+(ly/fp.semi_minor)**2)


def density_multiplier(fp:FormationFootprint, world_pos:Vec2, center:Vec2)->float:
    r=normalized_ellipse_radius(fp,world_pos,center)
    if r>1.0:return 0.0
    if fp.density_model=="UNIFORM":return 1.0
    # Formation center is somewhat denser, but not a point-mass. Average is around 1.
    return max(0.35,1.45-0.85*r*r)


def sample_person_position(sim, unit:Unit, fp:FormationFootprint)->Vec2:
    # Uniform-in-area ellipse, then mild center bias for CENTER_WEIGHTED formations.
    u=sim.rng.random(); theta=2*math.pi*sim.rng.random()
    exponent=0.62 if fp.density_model=="CENTER_WEIGHTED" else 0.5
    r=u**exponent
    local=(fp.semi_major*r*math.cos(theta),fp.semi_minor*r*math.sin(theta))
    return world_from_local(unit.pos,fp.orientation_deg,local)


def equipment_item_position(unit:Unit, element:FormationElement, item_index:int, fp:FormationFootprint)->Vec2:
    n=max(1,len(element.item_states) if element.item_states else element.initial_count or element.count)
    # Deterministic low-count layout. For tanks/guns this approximates an internal tactical spread
    # without deaggregating every item into a full simulation entity.
    if n==1: local=(0.0,0.0)
    elif n==2:
        xs=(-0.35,0.35); local=(xs[item_index%2]*fp.length_m,0.0)
    elif n<=4:
        pts=[(-0.28,-0.26),(0.28,-0.26),(-0.42,0.24),(0.42,0.24)]
        x,y=pts[item_index%4]; local=(x*fp.length_m,y*fp.width_m)
    elif n<=6:
        pts=[(-.34,-.3),(0,-.34),(.34,-.3),(-.34,.28),(0,.34),(.34,.28)]
        x,y=pts[item_index%6]; local=(x*fp.length_m,y*fp.width_m)
    else:
        cols=max(2,int(math.ceil(math.sqrt(n))))
        row=item_index//cols; col=item_index%cols
        rows=max(1,int(math.ceil(n/cols)))
        x=(col-(cols-1)/2)/max(1,cols-1)*.7*fp.length_m
        y=(row-(rows-1)/2)/max(1,rows-1)*.7*fp.width_m
        local=(x,y)
    return world_from_local(unit.pos,fp.orientation_deg,local)
