from __future__ import annotations
import argparse
import math
import os
from dataclasses import dataclass
from typing import Tuple

import pygame

from mnsim.scenario import load_scenario
from mnsim.model import Side, UnitState
from mnsim.application import SimulationController, DEFAULT_SIM_SPEEDS
from mnsim.symbology import echelon_amplifier, branch_symbol_kind
from mnsim.mounted import transport_capacity, free_seats, required_vehicle_crew, crew_available
from mnsim.cartography import contour_label_candidate

# -----------------------------------------------------------------------------
# Responsive CMO-inspired tactical UI.
# The simulation kernel remains independent from pygame.
# -----------------------------------------------------------------------------
DEFAULT_W, DEFAULT_H = 1600, 950
MIN_W, MIN_H = 900, 650
FPS = 60

# Palette
MAP_BG = (218, 218, 191)
MAP_BG_2 = (211, 214, 185)
CONTOUR = (198, 196, 166)
GRID = (175, 176, 159)
ROAD = (141, 133, 112)
WATER = (178, 206, 211)
PANEL = (29, 35, 39)
PANEL_2 = (34, 41, 46)
PANEL_3 = (44, 52, 58)
TEXT = (228, 232, 234)
MUTED = (154, 163, 168)
BLUE = (35, 81, 232)
RED = (235, 48, 54)
GREEN = (54, 199, 91)
YELLOW = (242, 196, 66)
CYAN = (44, 200, 208)                # ordinary sensor range
ORANGE = (241, 132, 65)              # small arms / machine-gun range
PURPLE = (120, 90, 190)              # counter-battery radar
MAGENTA = (190, 78, 170)             # guided anti-armor / missile range
GUN_RED = (210, 72, 62)              # direct-fire cannon / main-gun range
ARTY_GOLD = (190, 145, 55)           # indirect-fire / artillery range
BLACK = (20, 23, 24)

WORLD_W = WORLD_H = 4000.0


@dataclass
class Layout:
    width: int
    height: int
    map_rect: pygame.Rect
    bottom_rect: pygame.Rect
    panels: list[pygame.Rect]
    compact: bool


def build_layout(width: int, height: int) -> Layout:
    """Responsive layout.  At wide sizes panels are 1x4; otherwise 2x2."""
    width = max(MIN_W, width)
    height = max(MIN_H, height)
    compact = width < 1180
    if compact:
        bottom_h = max(300, int(height * 0.38))
    else:
        bottom_h = max(235, int(height * 0.27))
    bottom_h = min(bottom_h, height - 320)
    map_rect = pygame.Rect(0, 0, width, height - bottom_h)
    bottom_rect = pygame.Rect(0, map_rect.bottom, width, bottom_h)

    gap = 2
    panels: list[pygame.Rect] = []
    if compact:
        pw = (width - gap) // 2
        ph = (bottom_h - gap) // 2
        panels = [
            pygame.Rect(0, bottom_rect.top, pw, ph),
            pygame.Rect(pw + gap, bottom_rect.top, width - pw - gap, ph),
            pygame.Rect(0, bottom_rect.top + ph + gap, pw, bottom_h - ph - gap),
            pygame.Rect(pw + gap, bottom_rect.top + ph + gap, width - pw - gap, bottom_h - ph - gap),
        ]
    else:
        # ratios chosen to preserve the original information hierarchy
        ratios = [0.25, 0.26, 0.255, 0.235]
        x = 0
        for i, r in enumerate(ratios):
            if i == len(ratios) - 1:
                pw = width - x
            else:
                pw = int(width * r)
            panels.append(pygame.Rect(x, bottom_rect.top, max(1, pw - (gap if i < 3 else 0)), bottom_h))
            x += pw
    return Layout(width, height, map_rect, bottom_rect, panels, compact)


@dataclass
class Camera:
    cx: float = 2000.0
    cy: float = 2000.0
    zoom: float = 1.0

    def base_scale(self, map_rect: pygame.Rect) -> float:
        # pixels per metre at zoom=1.  A single scalar is used for X and Y,
        # guaranteeing square 500m grid cells regardless of window aspect ratio.
        return min(map_rect.w / WORLD_W, map_rect.h / WORLD_H)

    def scale(self, map_rect: pygame.Rect) -> float:
        return self.base_scale(map_rect) * self.zoom

    def visible_world(self, map_rect: pygame.Rect) -> Tuple[float, float]:
        s = max(self.scale(map_rect), 1e-9)
        return map_rect.w / s, map_rect.h / s

    def world_to_screen(self, p, map_rect: pygame.Rect):
        s = self.scale(map_rect)
        return (
            int(map_rect.centerx + (p[0] - self.cx) * s),
            int(map_rect.centery + (p[1] - self.cy) * s),
        )

    def screen_to_world(self, p, map_rect: pygame.Rect):
        s = max(self.scale(map_rect), 1e-9)
        return (
            self.cx + (p[0] - map_rect.centerx) / s,
            self.cy + (p[1] - map_rect.centery) / s,
        )

    def clamp(self, map_rect: pygame.Rect):
        vw, vh = self.visible_world(map_rect)
        if vw >= WORLD_W:
            self.cx = WORLD_W / 2
        else:
            self.cx = max(vw / 2, min(WORLD_W - vw / 2, self.cx))
        if vh >= WORLD_H:
            self.cy = WORLD_H / 2
        else:
            self.cy = max(vh / 2, min(WORLD_H - vh / 2, self.cy))

    def reset(self):
        self.cx, self.cy, self.zoom = WORLD_W / 2, WORLD_H / 2, 1.0


def set_world(sim):
    global WORLD_W, WORLD_H
    WORLD_W = float(sim.world.get("width_m", 4000))
    WORLD_H = float(sim.world.get("height_m", 4000))


def side_color(side):
    return BLUE if side == Side.BLUE else RED


def echelon_mark(echelon: str) -> str:
    return echelon_amplifier(echelon)


def _clip_text(font, text, max_w):
    if max_w <= 10:
        return ""
    if font.size(text)[0] <= max_w:
        return text
    while text and font.size(text + "…")[0] > max_w:
        text = text[:-1]
    return text + "…"



def _nice_metric_distance(raw_m: float) -> float:
    raw=max(1e-9,float(raw_m))
    exp=10.0**math.floor(math.log10(raw))
    frac=raw/exp
    if frac < 1.5: nice=1.0
    elif frac < 3.5: nice=2.0
    elif frac < 7.5: nice=5.0
    else: nice=10.0
    return nice*exp


def _format_metric(m: float) -> str:
    if m >= 1000.0:
        return f"{m/1000.0:g} km"
    return f"{m:g} m"


def _grid_interval_for_scale(px_per_m: float, target_px: float = 95.0) -> float:
    return _nice_metric_distance(target_px/max(px_per_m,1e-9))


def _draw_scale_bar(screen, map_rect, px_per_m: float, font):
    """Draw scale below the upper-left guidance/status text and away from bottom controls."""
    dist=_nice_metric_distance(125.0/max(px_per_m,1e-9))
    px=max(24,int(dist*px_per_m))
    label=_format_metric(dist); tw,th=font.size(label)
    # Keep the scale below the upper-left guidance/status line.  The lower-left is
    # reserved for interactive MAP/ICONS/RANGE/GRID/INFO/LOG controls.
    x=map_rect.left+18; y=map_rect.top+th+54
    back=pygame.Rect(x-7,y-th-17,max(px+14,tw+14),th+25)
    pygame.draw.rect(screen,(235,236,226),back,border_radius=3)
    pygame.draw.rect(screen,(75,78,72),back,1,border_radius=3)
    col=(35,38,35)
    pygame.draw.line(screen,col,(x,y),(x+px,y),3)
    pygame.draw.line(screen,col,(x,y-6),(x,y+2),2)
    pygame.draw.line(screen,col,(x+px,y-6),(x+px,y+2),2)
    screen.blit(font.render(label,True,col),(x,y-th-9))


def draw_terrain(screen, cam, small, layout, sim):
    """Draw scenario terrain. Terrain geometry is shared with the simulation mobility model."""
    mr=layout.map_rect
    pygame.draw.rect(screen,(42,45,42),mr)
    old_clip=screen.get_clip(); screen.set_clip(mr)

    tl=cam.world_to_screen((0,0),mr); br=cam.world_to_screen((WORLD_W,WORLD_H),mr)
    world_screen_rect=pygame.Rect(tl[0],tl[1],br[0]-tl[0],br[1]-tl[1])
    world_screen_rect.normalize()
    world_clip=mr.clip(world_screen_rect)
    pygame.draw.rect(screen,MAP_BG,world_clip)
    # map_rect is the whole tactical viewport, while world_clip is the actual authored world.
    # Rectangular maps are letterboxed at zoom=1, so clipping only to map_rect lets thick rivers,
    # roads and polygons bleed into the dark margins.  All world geometry must be clipped here.
    screen.set_clip(world_clip)

    # Simple open-ground tint patches are visual only; actual mobility currently distinguishes
    # OPEN/ROAD/BRIDGE/RIVER and deliberately ignores elevation.
    regions=[
        ([(0,400),(800,200),(1500,520),(1300,1200),(400,1400)],MAP_BG_2),
        ([(2450,0),(4000,0),(4000,1000),(3300,1100),(2850,650)],(215,216,188)),
    ]
    for pts,color in regions:
        pygame.draw.polygon(screen,color,[cam.world_to_screen(p,mr) for p in pts])

    terrain=getattr(sim,"terrain",None)
    if terrain:
        # Polygon terrain areas are data-driven and may later carry mobility/observation effects.
        area_colors={
            "WOODS":(116,137,95), "FOREST":(92,119,77), "BRUSH":(143,151,104),
            "URBAN":(168,164,153), "MARSH":(145,164,146), "OPEN":MAP_BG_2,
            "LAKE":(112,166,205), "BUILDING":(126,122,118), "ELEVATION":(205,190,154),
        }
        contour_labels=[];occupied_contour_labels=[]
        for area in getattr(terrain,"areas",[]):
            pts=[cam.world_to_screen(tuple(p),mr) for p in area.get("polygon",[])]
            if len(pts)>=3:
                atype=str(area.get("type","OPEN")).upper()
                color=tuple(area.get("ui_color",area_colors.get(atype,(190,190,175))))
                if atype=="ELEVATION":
                    pygame.draw.polygon(screen,color,pts,2)
                    base=small.render(f"{float(area.get('elevation_m',0)):g}m",True,(105,87,55))
                    cand=contour_label_candidate(pts,base.get_size(),occupied_contour_labels,6.0)
                    if cand:
                        center,angle,rect=cand;occupied_contour_labels.append(rect);contour_labels.append((base,center,angle))
                else:
                    if atype=="BUILDING" and bool(area.get("destroyed",False)):color=(84,80,78)
                    pygame.draw.polygon(screen,color,pts)
                    if atype=="BUILDING":
                        pygame.draw.polygon(screen,(75,72,70),pts,2)
                        if bool(area.get("destroyed",False)):
                            r=pygame.Rect(min(x for x,y in pts),min(y for x,y in pts),max(x for x,y in pts)-min(x for x,y in pts),max(y for x,y in pts)-min(y for x,y in pts))
                            pygame.draw.line(screen,(155,55,45),r.topleft,r.bottomright,2);pygame.draw.line(screen,(155,55,45),r.topright,r.bottomleft,2)
                if atype=="FOREST":
                    # Sparse canopy marks distinguish dense forest from lighter WOODS without
                    # introducing image assets or an editor/runtime format mismatch.
                    minx,maxx=min(q[0] for q in pts),max(q[0] for q in pts)
                    miny,maxy=min(q[1] for q in pts),max(q[1] for q in pts)
                    step=24
                    for yy in range(miny+10,maxy,step):
                        for xx in range(minx+10,maxx,step):
                            wp=cam.screen_to_world((xx,yy),mr)
                            if terrain.area_at(wp) and any(str(a.get("type","")).upper()=="FOREST" for a in terrain.area_at(wp)):
                                pygame.draw.circle(screen,(65,94,58),(xx,yy),2)
        for base,center,angle in contour_labels:
            surf=pygame.transform.rotate(base,-angle);r=surf.get_rect(center=(round(center[0]),round(center[1])))
            pygame.draw.rect(screen,(232,226,199),r.inflate(4,2),border_radius=2);screen.blit(surf,r)
        # HESCO/MIL1-style barriers. heading_deg is the outward normal; the short red tick shows
        # the exposed/facing side, so protected troops belong on the opposite side.
        for wall in getattr(terrain,"barricades",[]):
            a,b=terrain.barricade_endpoints(wall);sa=cam.world_to_screen(a,mr);sb=cam.world_to_screen(b,mr);c=cam.world_to_screen(tuple(wall.get("center",(0,0))),mr)
            pygame.draw.line(screen,(112,91,58),sa,sb,max(3,int(float(wall.get("width_m",1.06))*cam.scale(mr))))
            h=math.radians(float(wall.get("heading_deg",0.0)));tip=(int(c[0]+math.cos(h)*15),int(c[1]+math.sin(h)*15))
            pygame.draw.line(screen,(155,68,50),c,tip,2);pygame.draw.circle(screen,(155,68,50),tip,3)

        # Rivers may be authored either as legacy polygons or as a polyline + width.  The latter
        # makes bends/meanders easy to create in scenario data without hand-building both banks.
        for rv in terrain.rivers:
            poly=[cam.world_to_screen(tuple(p),mr) for p in rv.get("polygon",[])]
            if len(poly)>=3:
                pygame.draw.polygon(screen,WATER,poly)
            else:
                pts=[cam.world_to_screen(tuple(p),mr) for p in rv.get("points",[])]
                if len(pts)>=2:
                    width=max(2,int(float(rv.get("width_m",120))*cam.scale(mr)))
                    pygame.draw.lines(screen,WATER,False,pts,width)
                    # Round the joints so segmented rivers read visually as a continuous channel.
                    r=max(1,width//2)
                    for q in pts[1:-1]: pygame.draw.circle(screen,WATER,q,r)
        # Roads.
        for road in terrain.roads:
            pts=[cam.world_to_screen(tuple(p),mr) for p in road.get("points",[])]
            if len(pts)>=2:
                width=max(2,int(float(road.get("width_m",30))*cam.scale(mr)))
                pygame.draw.lines(screen,ROAD,False,pts,width)
        # Bridges drawn on top of rivers. New maps use an authored centerline polyline;
        # legacy center/length/heading bridges are converted by TerrainModel.bridge_points().
        for b in terrain.bridges:
            pts=[cam.world_to_screen(p,mr) for p in terrain.bridge_points(b)]
            if len(pts)<2: continue
            width=max(3,int(float(b.get("width_m",80))*cam.scale(mr)))
            destroyed=not terrain.bridge_operational(b)
            fill=(83,71,65) if destroyed else (132,119,91)
            pygame.draw.lines(screen,fill,False,pts,width)
            # Rounded joints make multi-segment authored bridges visually continuous.
            for q in pts[1:-1]:pygame.draw.circle(screen,fill,q,max(1,width//2))
            pygame.draw.lines(screen,(76,70,58),False,pts,max(1,min(2,width)))
            if destroyed:
                # Mark destruction across the midpoint rather than assuming a rectangular deck.
                i=max(0,(len(pts)-1)//2); a,c=pts[i],pts[i+1]
                dx,dy=c[0]-a[0],c[1]-a[1]; d=max(math.hypot(dx,dy),1.0); mx,my=(a[0]+c[0])//2,(a[1]+c[1])//2
                nx,ny=-dy/d,dx/d; r=max(7,width//2+4)
                pygame.draw.line(screen,(175,55,45),(int(mx-nx*r),int(my-ny*r)),(int(mx+nx*r),int(my+ny*r)),3)

    # Dynamic metric grid.  Keep approximately one grid line every ~95 pixels and snap the
    # real-world spacing to cartographic 1/2/5 x 10^n values.
    scale=max(cam.scale(mr),1e-9)
    step=_grid_interval_for_scale(scale)
    vw,vh=cam.visible_world(mr)
    left=max(0.0,cam.cx-vw/2); top=max(0.0,cam.cy-vh/2)
    x=math.floor(left/step)*step
    while x<=WORLD_W+1e-6:
        a=cam.world_to_screen((x,0),mr); b=cam.world_to_screen((x,WORLD_H),mr)
        pygame.draw.line(screen,GRID,a,b,1)
        x+=step
    y=math.floor(top/step)*step
    while y<=WORLD_H+1e-6:
        a=cam.world_to_screen((0,y),mr); b=cam.world_to_screen((WORLD_W,y),mr)
        pygame.draw.line(screen,GRID,a,b,1)
        y+=step
    # UI annotations belong to the tactical viewport, not to the world geometry clip.
    screen.set_clip(mr)
    grid_label=small.render(f"Grid {_format_metric(step)}",True,(75,78,72))
    screen.blit(grid_label,(mr.right-grid_label.get_width()-12,mr.top+10))
    _draw_scale_bar(screen,mr,scale,small)

    screen.set_clip(old_clip)

def draw_objectives(screen, cam, sim, font, layout):
    mr = layout.map_rect
    old_clip = screen.get_clip(); screen.set_clip(mr)
    for name, p in sim.objectives.items():
        x, y = cam.world_to_screen(tuple(p), mr)
        if mr.collidepoint(x, y):
            pygame.draw.circle(screen, YELLOW, (x,y), 14, 2)
            pygame.draw.line(screen, YELLOW, (x-18,y),(x+18,y),1)
            pygame.draw.line(screen, YELLOW, (x,y-18),(x,y+18),1)
            screen.blit(font.render(name, True, (115,100,35)), (x+18,y-10))
    screen.set_clip(old_clip)


def symbol_quantity(u):
    return u.personnel if u.branch == "INFANTRY" else u.equipment


def _unit_symbol_anchor(sim, cam, u, layout):
    """Return the screen anchor used for a unit symbol.

    Detached vehicles are physically created at exactly the parent's damage position.  When the
    residual formation is still halted/engaging there, drawing both NATO symbols at the same pixel
    makes the later-drawn detached vehicle completely hide the residual formation.  Keep the world
    coordinates unchanged and only offset the detached *display* symbol while the two anchors would
    overlap.  A leader line preserves the true physical location.
    """
    mr=layout.map_rect
    true_xy=cam.world_to_screen(u.pos,mr)
    parent_id=u.metadata.get("detached_from")
    if not parent_id or parent_id not in sim.units:
        return true_xy, true_xy
    parent=sim.units[parent_id]
    parent_xy=cam.world_to_screen(parent.pos,mr)
    if math.dist(true_xy,parent_xy) >= 30:
        return true_xy, true_xy
    try:
        serial=int(str(u.uid).rsplit("-DET-",1)[1])
    except Exception:
        serial=1
    # Fan several co-located detached vehicles around the true damage point.  The parent remains at
    # the exact anchor so the residual platoon is always visible.
    offsets=((24,-18),(24,18),(-24,-18),(-24,18),(0,-26),(0,26))
    dx,dy=offsets[(serial-1)%len(offsets)]
    return (true_xy[0]+dx,true_xy[1]+dy), true_xy


def draw_nato_symbol(screen, cam, font, tiny, sim, u, selected, layout):
    mr = layout.map_rect
    (x, y), true_xy = _unit_symbol_anchor(sim,cam,u,layout)
    if not mr.inflate(100,100).collidepoint(x,y):
        return
    if (x,y) != true_xy:
        pygame.draw.line(screen,(105,105,96),true_xy,(x,y),1)
        pygame.draw.circle(screen,(105,105,96),true_xy,3,1)
    ineffective = u.state == UnitState.COMBAT_INEFFECTIVE
    c = (220,155,45) if ineffective else side_color(u.side) if u.alive else (90,90,90)
    rw, rh = 44, 27
    rect = pygame.Rect(x-rw//2, y-rh//2, rw, rh)
    if u.side == Side.BLUE:
        pygame.draw.rect(screen, c, rect, 2)
    else:
        pts = [(x, y-rh//2-2),(x+rw//2+2,y),(x,y+rh//2+2),(x-rw//2-2,y)]
        pygame.draw.polygon(screen, c, pts, 2)
    symbol_kind = branch_symbol_kind(u.branch)
    if symbol_kind == "INFANTRY":
        pygame.draw.line(screen,c,(rect.left+5,rect.top+4),(rect.right-5,rect.bottom-4),2)
        pygame.draw.line(screen,c,(rect.right-5,rect.top+4),(rect.left+5,rect.bottom-4),2)
    elif symbol_kind == "MOTORIZED_INFANTRY":
        pygame.draw.line(screen,c,(rect.left+5,rect.top+4),(rect.right-5,rect.bottom-4),2)
        pygame.draw.line(screen,c,(rect.right-5,rect.top+4),(rect.left+5,rect.bottom-4),2)
        pygame.draw.circle(screen,c,(x-10,rect.bottom+1),2,1)
        pygame.draw.circle(screen,c,(x+10,rect.bottom+1),2,1)
    elif symbol_kind == "MECH_INFANTRY":
        pygame.draw.ellipse(screen,c,pygame.Rect(x-13,y-7,26,14),2)
        pygame.draw.line(screen,c,(rect.left+6,rect.top+4),(rect.right-6,rect.bottom-4),2)
        pygame.draw.line(screen,c,(rect.right-6,rect.top+4),(rect.left+6,rect.bottom-4),2)
    elif symbol_kind == "ARMOR":
        pygame.draw.ellipse(screen,c,pygame.Rect(x-13,y-7,26,14),2)
    elif symbol_kind == "ARTILLERY":
        pygame.draw.circle(screen,c,(x,y),4,2)
    amp = echelon_mark(u.echelon)
    if amp:
        t=tiny.render(amp,True,c); screen.blit(t,(x-t.get_width()//2,rect.top-t.get_height()-1))
    if selected:
        pygame.draw.rect(screen, YELLOW, rect.inflate(12,12), 2)
    ratio = max(0,min(1,u.strength_ratio))
    pygame.draw.rect(screen, (70,70,65), (rect.left, rect.bottom+4, rw, 3))
    pygame.draw.rect(screen, (220,155,45) if ineffective else GREEN, (rect.left, rect.bottom+4, int(rw*ratio), 3))
    label = f"{u.name}  x{symbol_quantity(u)}"
    if ineffective:
        label += "  COMBAT INEFFECTIVE"
    txt = font.render(label, True, BLACK)
    bg = pygame.Surface((txt.get_width()+4, txt.get_height()+2), pygame.SRCALPHA); bg.fill((225,225,207,185))
    screen.blit(bg,(x-txt.get_width()//2-2,rect.bottom+9)); screen.blit(txt,(x-txt.get_width()//2,rect.bottom+10))


def draw_contact(screen, cam, font, tiny, tr, sim, viewer_side, layout):
    inferred = (tr.state == "LOST" and getattr(tr,"existence_confirmed",False))
    if tr.state == "LOST" and not inferred:
        return
    mr = layout.map_rect
    x,y=cam.world_to_screen(tr.estimated_pos, mr)
    if not mr.inflate(100,100).collidepoint(x,y):
        return
    c=RED if viewer_side==Side.BLUE else BLUE
    age=max(0.0,sim.time-tr.last_seen_time)
    er=max(4,int(tr.position_error_m*cam.scale(mr)))
    pygame.draw.circle(screen,(115,105,96),(x,y),er,1)
    if inferred:
        pts=[(x,y-13),(x+22,y),(x,y+13),(x-22,y)]
        pygame.draw.polygon(screen,(125,90,85),pts,1)
        cls=tr.classification if tr.classification!="UNKNOWN" else "ENEMY"
        label=f"EST-{cls}  existence {tr.belief_confidence:.0%}  last {age:.0f}s"
        t=font.render(label,True,BLACK)
        bg=pygame.Surface((t.get_width()+4,t.get_height()+2),pygame.SRCALPHA); bg.fill((225,225,207,185))
        screen.blit(bg,(x-t.get_width()//2-2,y+18)); screen.blit(t,(x-t.get_width()//2,y+19))
        return
    if tr.state == "DETECTED" or tr.classification == "UNKNOWN":
        pygame.draw.circle(screen,c,(x,y),8,2); label=f"CONTACT  {tr.confidence:.0%}  {age:.0f}s"
    else:
        pts=[(x,y-13),(x+22,y),(x,y+13),(x-22,y)]; pygame.draw.polygon(screen,c,pts,2)
        contact_kind = branch_symbol_kind(tr.classification)
        if contact_kind == "INFANTRY":
            pygame.draw.line(screen,c,(x-12,y-7),(x+12,y+7),2); pygame.draw.line(screen,c,(x+12,y-7),(x-12,y+7),2)
        elif contact_kind == "MOTORIZED_INFANTRY":
            pygame.draw.line(screen,c,(x-12,y-7),(x+12,y+7),2); pygame.draw.line(screen,c,(x+12,y-7),(x-12,y+7),2)
            pygame.draw.circle(screen,c,(x-8,y+10),2,1); pygame.draw.circle(screen,c,(x+8,y+10),2,1)
        elif contact_kind == "MECH_INFANTRY":
            pygame.draw.ellipse(screen,c,pygame.Rect(x-12,y-6,24,12),2)
            pygame.draw.line(screen,c,(x-11,y-7),(x+11,y+7),2); pygame.draw.line(screen,c,(x+11,y-7),(x-11,y+7),2)
        elif contact_kind == "ARMOR":
            pygame.draw.ellipse(screen,c,pygame.Rect(x-12,y-6,24,12),2)
        elif contact_kind == "ARTILLERY":
            pygame.draw.circle(screen,c,(x,y),4,2)
        ident = tr.target_id if tr.state == "IDENTIFIED" else tr.classification
        if tr.source == "COUNTER_BATTERY": ident = f"CB ORIGIN {ident}"
        label=f"{ident}  {tr.confidence:.0%}  ±{tr.position_error_m:.0f}m  {age:.0f}s"
    t=font.render(label,True,BLACK)
    bg=pygame.Surface((t.get_width()+4,t.get_height()+2),pygame.SRCALPHA); bg.fill((225,225,207,185))
    screen.blit(bg,(x-t.get_width()//2-2,y+18)); screen.blit(t,(x-t.get_width()//2,y+19))


def _range_badge(screen, anchor, color, size=13):
    """Draw a larger high-contrast badge and return its center.

    Primitive pygame geometry avoids Unicode/emoji rendering differences across systems.
    """
    x, y = int(anchor[0]), int(anchor[1])
    pygame.draw.circle(screen, (238, 239, 224), (x, y), size)
    pygame.draw.circle(screen, BLACK, (x, y), size, 2)
    return x, y


def _draw_range_symbol(screen, anchor, kind, color):
    """Readable semantic symbol drawn directly on a selected-unit range ring."""
    x, y = _range_badge(screen, anchor, color, 13)
    kind = str(kind).upper()
    if kind == "EYE":
        pygame.draw.arc(screen, color, pygame.Rect(x-10, y-6, 20, 12), math.pi, 2*math.pi, 3)
        pygame.draw.arc(screen, color, pygame.Rect(x-10, y-6, 20, 12), 0, math.pi, 3)
        pygame.draw.circle(screen, color, (x, y), 3)
    elif kind == "RADAR":
        pygame.draw.line(screen, color, (x, y+8), (x, y-4), 3)
        pygame.draw.line(screen, color, (x-6, y+8), (x+6, y+8), 2)
        pygame.draw.arc(screen, color, pygame.Rect(x-1, y-9, 11, 11), -math.pi/2, math.pi/2, 2)
        pygame.draw.arc(screen, color, pygame.Rect(x-3, y-12, 17, 17), -math.pi/2, math.pi/2, 2)
    elif kind == "SHELL":
        # Indirect-fire artillery shell, deliberately vertical to differ from cannon ammunition.
        pts=[(x-4,y+8),(x-5,y-3),(x-3,y-8),(x,y-11),(x+3,y-8),(x+5,y-3),(x+4,y+8)]
        pygame.draw.polygon(screen, color, pts)
        pygame.draw.line(screen, (238,239,224), (x-3,y+3), (x+3,y+3), 2)
    elif kind == "CANNON":
        # Direct-fire tank/cannon round.
        pygame.draw.rect(screen, color, pygame.Rect(x-8, y-3, 10, 6), border_radius=2)
        pygame.draw.polygon(screen, color, [(x+2,y-3),(x+9,y),(x+2,y+3)])
        pygame.draw.line(screen, (238,239,224), (x-4,y-3), (x-4,y+3), 1)
    elif kind == "MG":
        # compact tripod/receiver silhouette for a machine-gun range ring
        pygame.draw.line(screen, color, (x-8,y), (x+7,y), 4)
        pygame.draw.line(screen, color, (x+7,y), (x+11,y-1), 2)
        pygame.draw.line(screen, color, (x-2,y+2), (x-7,y+9), 2)
        pygame.draw.line(screen, color, (x-1,y+2), (x+4,y+9), 2)
    elif kind == "MISSILE":
        pygame.draw.line(screen, color, (x-7,y+5), (x+5,y-4), 4)
        pygame.draw.polygon(screen, color, [(x+4,y-7),(x+10,y-4),(x+6,y+2)])
        pygame.draw.line(screen, color, (x-7,y+5), (x-10,y+8), 2)
        pygame.draw.line(screen, color, (x-6,y+4), (x-9,y+1), 2)
    else:
        pygame.draw.rect(screen, color, pygame.Rect(x-9, y-3, 12, 6), border_radius=3)
        pygame.draw.polygon(screen, color, [(x+3,y-3),(x+10,y),(x+3,y+3)])


def _weapon_range_family(weapon):
    """Return a stable semantic UI family; metadata is authoritative when supplied."""
    meta=getattr(weapon, "metadata", {}) or {}
    family=str(meta.get("ui_range_family", "")).upper()
    if family in {"SMALL_ARMS", "MACHINE_GUN", "CANNON", "GUIDED_AT", "INDIRECT_FIRE"}:
        return family
    cap=str(getattr(weapon, "capability", "")).upper()
    name=str(getattr(weapon, "name", "")).upper()
    if cap == "INDIRECT_FIRE":
        return "INDIRECT_FIRE"
    if any(token in name for token in ("GUIDED", "MISSILE", "ATGM", "JAVELIN")):
        return "GUIDED_AT"
    if any(token in name for token in ("MAIN GUN", "CANNON", "AUTOCANNON")):
        return "CANNON"
    return "SMALL_ARMS"



def _weapon_range_label(weapon):
    """Short data-driven label shown beside a weapon range badge."""
    meta=getattr(weapon, "metadata", {}) or {}
    explicit=str(meta.get("ui_range_label", "")).strip().upper()
    if explicit:
        return explicit
    family=_weapon_range_family(weapon)
    return {
        "SMALL_ARMS": "SA",
        "MACHINE_GUN": "MG",
        "CANNON": "CANNON",
        "GUIDED_AT": "ATGM",
        "INDIRECT_FIRE": "ARTY",
    }.get(family, "WPN")


def _format_range_m(range_m):
    r=int(round(float(range_m)))
    return f"{r/1000:.1f}km" if r >= 1000 else f"{r}m"


def _draw_range_label(screen, font, anchor, text, color, ring_angle_deg=0.0):
    """Draw a tangential caption whose *head* starts just above the range icon.

    The icon remains upright.  The caption sits on the local tangent instead of
    beside the icon radially: at the preferred right-hand/0-degree anchor the
    first character is immediately above the icon and the text runs vertically
    upward.  This is intentionally a ``symbol -> caption`` stack so neighboring
    concentric rings do not place long text badges side-by-side.
    """
    if not font or not anchor or not text:
        return

    text_surf=font.render(str(text), True, BLACK)
    pad_x,pad_y=5,2
    badge=pygame.Surface(
        (text_surf.get_width()+2*pad_x, text_surf.get_height()+2*pad_y),
        pygame.SRCALPHA,
    )
    badge.fill((238,239,224,245))
    pygame.draw.rect(badge,color,badge.get_rect(),2,border_radius=4)
    badge.blit(text_surf,(pad_x,pad_y))

    # Pygame positive rotation is counter-clockwise.  At ring angle 0, +90 deg
    # turns left-to-right text into bottom-to-top text, so the text head (left
    # edge before rotation) is the end nearest the symbol and the caption grows
    # upward from it.
    tangent=(float(ring_angle_deg)+90.0) % 360.0
    if 90.0 < tangent <= 270.0:
        tangent -= 180.0
    rotated=pygame.transform.rotate(badge, tangent)

    # Place the badge along the tangent, not along the radius.  The nearest end
    # of the rotated badge starts just beyond the icon.  For the normal 0-degree
    # anchor this means: icon at the ring, caption head directly above it.
    # Screen-space tangent for a ring angle a is (-sin(a), -cos(a)); at a=0 it
    # points straight upward.  rotated.get_height() is the long dimension for a
    # vertical label at the preferred anchor.
    a=math.radians(float(ring_angle_deg))
    tx=-math.sin(a)
    ty=-math.cos(a)
    icon_radius=13.0
    gap=4.0
    half_extent=max(rotated.get_width(), rotated.get_height())/2.0
    offset=icon_radius + gap + half_extent
    cx=float(anchor[0]) + tx*offset
    cy=float(anchor[1]) + ty*offset
    rect=rotated.get_rect(center=(int(round(cx)),int(round(cy))))
    screen.blit(rotated,rect)

def _family_visual(family):
    family=str(family).upper()
    return {
        "SMALL_ARMS": (ORANGE, "BULLET"),
        "MACHINE_GUN": (ORANGE, "MG"),
        "CANNON": (GUN_RED, "CANNON"),
        "GUIDED_AT": (MAGENTA, "MISSILE"),
        "INDIRECT_FIRE": (ARTY_GOLD, "SHELL"),
    }.get(family, (ORANGE, "BULLET"))


def _ring_symbol_anchor(center, radius_px, map_rect, lane=0):
    """Return (anchor, radial-angle) on a visible part of the ring.

    The angle is also used to orient the accompanying caption along the local
    tangent.  The right-hand/0-degree position remains preferred.
    """
    inset=18
    candidates=(0, -35, 35, -70, 70, 180)
    for deg in candidates:
        a=math.radians(deg)
        x=center[0] + math.cos(a)*(radius_px+15)
        y=center[1] + math.sin(a)*(radius_px+15) + lane*29
        if (map_rect.left+inset <= x <= map_rect.right-inset and
                map_rect.top+inset <= y <= map_rect.bottom-inset):
            return (int(x),int(y)), float(deg)
    return None, None


def _visual_profile_for_ui(unit, sim=None):
    # Use the same data-driven profile as the engine so F1/properties/map never drift from
    # branch defaults or weather/illumination modifiers. Terrain crossed farther down a sightline
    # can further degrade detection per target and therefore cannot be represented by one perfect arc.
    if sim is not None and hasattr(sim, "visual_sensor_profile"):
        forward,fov,close,_,_=sim.visual_sensor_profile(unit, None)
        return float(forward),float(fov),float(close)
    md=dict(unit.unit_type.metadata.get("visual_sensor", {}))
    return (float(md.get("forward_range_m", unit.unit_type.detection_range_m)),
            float(md.get("forward_fov_deg", 80.0)),
            float(md.get("all_round_awareness_m", 180.0)))

def _terrain_limited_visual_distance(sim, unit, heading_deg, max_range_m, awareness=False):
    """Approximate the currently usable visual distance along one map ray.

    This is UI-only and deliberately asks the same terrain observation contract used by the
    simulation instead of inventing a second forest/building rule in main.py.  Weather and
    illumination are already folded into ``max_range_m`` by ``_visual_sensor_profile``.
    Vegetation/building/smoke LOS layers that return range/detection blocking through
    ``terrain.observation_modifier`` therefore become visible automatically.
    """
    if sim is None or getattr(sim, "terrain", None) is None:
        return float(max_range_m)
    terrain=sim.terrain
    if not hasattr(terrain, "observation_modifier"):
        return float(max_range_m)
    sensor_mode=str(dict(unit.unit_type.metadata.get("visual_sensor", {})).get("sensor_mode", "VISUAL")).upper()
    # Prefer the UI-specific broad-phase query: one polygon pass per radial ray instead of dozens
    # of full observation_modifier() calls. Engine sensing remains unchanged.
    if hasattr(terrain,"approx_visual_limit"):
        return float(terrain.approx_visual_limit(unit.pos,heading_deg,max_range_m,sensor_mode=sensor_mode,awareness=awareness))
    a=math.radians(float(heading_deg))
    q=(unit.pos[0]+math.cos(a)*float(max_range_m),unit.pos[1]+math.sin(a)*float(max_range_m))
    raw=terrain.observation_modifier(unit.pos,q,sensor_mode=sensor_mode)
    if float(raw.get("detection_factor",1.0))<=1e-9 or float(raw.get("range_factor",1.0))<=1e-9:return 0.0
    factor=float(raw.get("awareness_factor" if awareness else "range_factor",1.0))
    return float(max_range_m)*max(0.0,min(1.0,factor))


def _draw_actual_visual_footprint(screen, cam, map_rect, unit, sim, max_range_m, heading_deg, fov_deg, color, awareness=False):
    """Draw a terrain-clipped visual footprint for the selected unit."""
    if max_range_m <= 1.0:
        return
    center=cam.world_to_screen(unit.pos,map_rect); scale=cam.scale(map_rect)
    if awareness:
        start,end,steps=0.0,360.0,72
    else:
        half=max(1.0,float(fov_deg)*0.5)
        start,end=float(heading_deg)-half,float(heading_deg)+half
        steps=max(18,int(abs(fov_deg)/3.0))
    pts=[]
    for i in range(steps+1):
        deg=start+(end-start)*i/steps
        rr=_terrain_limited_visual_distance(sim,unit,deg,max_range_m,awareness=awareness)
        a=math.radians(deg)
        q=(unit.pos[0]+math.cos(a)*rr,unit.pos[1]+math.sin(a)*rr)
        pts.append(cam.world_to_screen(q,map_rect))
    if awareness:
        if len(pts)>2:
            pygame.draw.lines(screen,color,True,pts,2)
    else:
        if pts:
            pygame.draw.line(screen,color,center,pts[0],2)
            if len(pts)>1: pygame.draw.lines(screen,color,False,pts,2)
            pygame.draw.line(screen,color,center,pts[-1],2)


def _draw_visual_sector(screen, center, radius_px, heading_deg, fov_deg, color):
    """Legacy ideal sector used only when no simulation/terrain context is available."""
    if radius_px <= 2:
        return
    half=max(1.0,float(fov_deg)*0.5)
    start=float(heading_deg)-half; end=float(heading_deg)+half
    steps=max(12,int(abs(fov_deg)/5.0))
    pts=[]
    for i in range(steps+1):
        a=math.radians(start+(end-start)*i/steps)
        pts.append((int(center[0]+math.cos(a)*radius_px),int(center[1]+math.sin(a)*radius_px)))
    if pts:
        pygame.draw.line(screen,color,center,pts[0],2)
        if len(pts)>1:
            pygame.draw.lines(screen,color,False,pts,2)
        pygame.draw.line(screen,color,center,pts[-1],2)

def draw_ranges(screen, cam, selected, layout, label_font=None, sim=None):
    if not selected or not selected.active:
        return
    mr = layout.map_rect
    center = cam.world_to_screen(selected.pos, mr)
    s = cam.scale(mr)

    forward_m,fov_deg,all_round_m=_visual_profile_for_ui(selected,sim)
    det_px = int(forward_m * s)
    close_px = int(all_round_m * s)
    if det_px > 2:
        if sim is not None:
            _draw_actual_visual_footprint(screen,cam,mr,selected,sim,forward_m,selected.watch_heading_deg,fov_deg,CYAN,awareness=False)
        else:
            _draw_visual_sector(screen,center,det_px,selected.watch_heading_deg,fov_deg,CYAN)
        # Put the long-range eye/label at the centerline tip of the viewing sector.
        a=math.radians(selected.watch_heading_deg)
        anchor=(int(center[0]+math.cos(a)*(det_px+15)),int(center[1]+math.sin(a)*(det_px+15)))
        if mr.collidepoint(anchor):
            _draw_range_symbol(screen,anchor,"EYE",CYAN)
            _draw_range_label(screen,label_font,anchor,f"VIS {_format_range_m(forward_m)} / {fov_deg:.0f}deg",CYAN,selected.watch_heading_deg)
    if close_px > 2:
        if sim is not None:
            _draw_actual_visual_footprint(screen,cam,mr,selected,sim,all_round_m,selected.watch_heading_deg,360.0,CYAN,awareness=True)
        else:
            pygame.draw.circle(screen,CYAN,center,close_px,1)
        anchor,ring_angle=_ring_symbol_anchor(center,close_px,mr,-1)
        if anchor:
            _draw_range_symbol(screen,anchor,"EYE",CYAN)
            _draw_range_label(screen,label_font,anchor,f"360 {_format_range_m(all_round_m)}",CYAN,ring_angle)

    radar_lane=0
    for elem in selected.elements.values():
        if elem.operational and elem.role.upper() == "COUNTER_BATTERY_RADAR":
            rr = float(elem.metadata.get("radar_range_m", 0.0)); rp = int(rr*s)
            if rp > 2:
                pygame.draw.circle(screen, PURPLE, center, rp, 2)
                radar_lane = -1 if abs(rp-det_px) < 18 else 0
                anchor,ring_angle=_ring_symbol_anchor(center, rp, mr, radar_lane)
                if anchor:
                    _draw_range_symbol(screen, anchor, "RADAR", PURPLE)
                    _draw_range_label(screen, label_font, anchor, f"CB RADAR {_format_range_m(rr)}", PURPLE, ring_angle)

    # Stable semantic colors: weapon count/order never changes the meaning of a color.
    # Multiple ranges in the same family deliberately reuse the same color/icon.
    groups={}
    for _, weapon in selected.operational_weapons():
        family=_weapon_range_family(weapon)
        key=(int(weapon.range_m), family)
        groups.setdefault(key, []).append(weapon)

    by_range={}
    for (rng,fam) in groups:
        by_range.setdefault(rng, []).append(fam)

    for rng in sorted(by_range):
        families=sorted(by_range[rng], key=lambda f: {"SMALL_ARMS":0,"MACHINE_GUN":1,"CANNON":2,"GUIDED_AT":3,"INDIRECT_FIRE":4}.get(f,9))
        base_rp=int(rng*s)
        if base_rp <= 2:
            continue
        n=len(families)
        for idx,family in enumerate(families):
            # If two families have exactly the same physical range, offset only a few display pixels
            # so both semantic rings remain visible while preserving the represented range.
            display_offset=(idx-(n-1)/2.0)*3.0
            rp=max(3,int(round(base_rp+display_offset)))
            color,kind=_family_visual(family)
            pygame.draw.circle(screen, color, center, rp, 2)
            lane = idx
            if abs(rp-det_px) < 18:
                lane += 1
            anchor,ring_angle=_ring_symbol_anchor(center, rp, mr, lane)
            if anchor:
                _draw_range_symbol(screen, anchor, kind, color)
                weapons=groups.get((rng,family), [])
                names=[]
                for weapon in weapons:
                    label=_weapon_range_label(weapon)
                    if label not in names:
                        names.append(label)
                short="/".join(names[:2]) if names else family
                if len(names) > 2:
                    short += "+"
                _draw_range_label(screen, label_font, anchor, f"{short} {_format_range_m(rng)}", color, ring_angle)



def draw_recent_artillery(screen, cam, sim, layout):
    """Short-lived fire-mission/impact overlay for debugging and AAR inspection."""
    now=sim.time
    # Only scan a bounded tail; logs can become large during long accelerated runs.
    for rec in sim.logs[-500:]:
        age=now-float(rec.get("t",now))
        if age < 0 or age > 8.0:
            continue
        if rec.get("kind")=="INDIRECT_FIRE":
            aim=rec.get("aim")
            if not aim: continue
            p=cam.world_to_screen(tuple(aim),layout.map_rect)
            cep=float(rec.get("dispersion_cep_m",0.0))
            rad=max(2,int(cep*cam.scale(layout.map_rect)))
            # CEP/aiming solution ring.
            pygame.draw.circle(screen,(205,135,45),p,rad,1)
            pygame.draw.line(screen,(205,135,45),(p[0]-5,p[1]),(p[0]+5,p[1]),1)
            pygame.draw.line(screen,(205,135,45),(p[0],p[1]-5),(p[0],p[1]+5),1)
        elif rec.get("kind")=="ARTY_IMPACT":
            pos=rec.get("pos")
            if not pos: continue
            p=cam.world_to_screen(tuple(pos),layout.map_rect)
            # Generic personnel-effect-radius visualization for the demo.
            rr=max(3,int(50.0*cam.scale(layout.map_rect)))
            pygame.draw.circle(screen,(185,85,45),p,rr,1)
            pygame.draw.circle(screen,(185,85,45),p,3,1)

def draw_orders_and_engagements(screen, cam, sim, view_mode, layout):
    mr = layout.map_rect
    old_clip=screen.get_clip(); screen.set_clip(mr)
    for u in sim.units.values():
        if not u.active or not u.current_order: continue
        if view_mode != "GOD" and u.side.value != view_mode: continue
        if u.current_order.kind in ("MOVE","ATTACK","RETREAT"):
            d=u.current_order.params.get("destination")
            if d:
                a=cam.world_to_screen(u.pos,mr); b=cam.world_to_screen(tuple(d),mr)
                pygame.draw.line(screen, side_color(u.side), a, b, 2)
                ang=math.atan2(b[1]-a[1],b[0]-a[0])
                p1=(b[0]-10*math.cos(ang-.45),b[1]-10*math.sin(ang-.45)); p2=(b[0]-10*math.cos(ang+.45),b[1]-10*math.sin(ang+.45))
                pygame.draw.line(screen,side_color(u.side),b,p1,2); pygame.draw.line(screen,side_color(u.side),b,p2,2)
    for u in sim.units.values():
        if not u.active or not u.target_id or u.target_id not in sim.units: continue
        if view_mode != "GOD" and u.side.value != view_mode: continue
        t=sim.units[u.target_id]
        if not t.active: continue
        if view_mode == "GOD": endpoint=t.pos
        else:
            tr=u.local_tracks.get(t.uid)
            if not tr or tr.state=="LOST": continue
            endpoint=tr.estimated_pos
        pygame.draw.line(screen, RED if u.side==Side.BLUE else BLUE, cam.world_to_screen(u.pos,mr), cam.world_to_screen(endpoint,mr), 1)
    if view_mode == "GOD":
        for e in sim.engagements:
            c=cam.world_to_screen(e["center"],mr)
            rad=max(16,int(float(sim.combat_config.get("engagement_link_m",420))*cam.scale(mr)))
            pygame.draw.circle(screen,(126,126,118),c,rad,1)
    screen.set_clip(old_clip)


SIM_SPEEDS = tuple(int(v) for v in DEFAULT_SIM_SPEEDS)

def speed_button_rects(layout):
    """Return semantic simulation-speed button rectangles for drawing and hit-testing."""
    mr=layout.map_rect
    button_w=43; gap=5
    total=len(SIM_SPEEDS)*button_w+(len(SIM_SPEEDS)-1)*gap
    x=mr.right-total-10
    return [(val, pygame.Rect(x+i*(button_w+gap), mr.top+10, button_w, 32))
            for i,val in enumerate(SIM_SPEEDS)]

def set_adjacent_speed(sim, direction):
    """Compatibility helper; generic speed policy lives in the UI-neutral controller."""
    SimulationController(sim, supported_speeds=SIM_SPEEDS).step_speed(direction)

def draw_map_buttons(screen, font, sim, cam, layout):
    mr=layout.map_rect
    labels=["MAP","ICONS","RANGE","GRID","INFO","LOG"]
    x=mr.left+10; y=mr.bottom-46
    for label in labels:
        rect=pygame.Rect(x,y,62,34); pygame.draw.rect(screen,(39,45,48),rect); pygame.draw.rect(screen,(68,77,82),rect,1)
        t=font.render(label,True,TEXT); screen.blit(t,(rect.centerx-t.get_width()//2,rect.centery-t.get_height()//2)); x+=68
    for val,rect in speed_button_rects(layout):
        pygame.draw.rect(screen,(38,44,47),rect)
        if sim.speed==val: pygame.draw.rect(screen,(99,114,122),rect,2)
        t=font.render(f"{val}x",True,TEXT); screen.blit(t,(rect.centerx-t.get_width()//2,rect.centery-t.get_height()//2))
    first_x=speed_button_rects(layout)[0][1].left
    zoomtxt=font.render(f"zoom x{cam.zoom:.1f}",True,(112,112,103))
    screen.blit(zoomtxt,(max(mr.left+8,first_x-zoomtxt.get_width()-12),mr.top+16))


def panel_title(screen, font, rect, title):
    pygame.draw.rect(screen,PANEL_2,rect); pygame.draw.line(screen,(59,68,73),(rect.left,rect.top),(rect.right,rect.top),1)
    screen.blit(font.render(title,True,TEXT),(rect.left+12,rect.top+9))


def draw_bottom(screen, fonts, sim, selected, layout):
    title, body, small, tiny = fonts
    pygame.draw.rect(screen,PANEL,layout.bottom_rect)
    p1,p2,p3,p4=layout.panels
    for r in (p1,p2,p3,p4): pygame.draw.rect(screen,PANEL_2,r); pygame.draw.rect(screen,(20,25,28),r,1)

    # Unit panel
    if selected:
        c=side_color(selected.side); pygame.draw.rect(screen,c,(p1.left+15,p1.top+15,min(82,p1.w//4),27))
        screen.blit(title.render(_clip_text(title,selected.name.upper(),p1.w-125),True,TEXT),(p1.left+111,p1.top+8))
        sy=p1.top+55
        data=[
            f"{selected.echelon}  {selected.branch}    TO&E {selected.unit_type.name}",
            f"Personnel {selected.personnel}/{selected.initial_personnel}    Equipment {selected.equipment}/{selected.initial_equipment}",
            f"Combat strength {selected.strength_ratio*100:.0f}%    State {selected.state.value}",
            *(([f"Mounted {selected.metadata.get('mount_state','-')}    Seats {free_seats(sim,selected)}/{transport_capacity(selected)}",
                 f"Vehicle crew {crew_available(selected)}/{required_vehicle_crew(selected)} required"]) if transport_capacity(selected)>0 else []),
            f"ANTI-ARMOR {'AVAILABLE' if selected.capability_available('ANTI_ARMOR') else 'UNAVAILABLE'}",
            f"Position {selected.pos[0]:.0f}, {selected.pos[1]:.0f} m",
            f"Mobility {selected.unit_type.metadata.get('mobility_class','-')}",
            f"Order {selected.current_order.kind if selected.current_order else '-'}",
        ]
        for s in data:
            if sy+small.get_height()>p1.bottom-8: break
            screen.blit(small.render(_clip_text(small,s,p1.w-24),True,TEXT),(p1.left+14,sy)); sy+=23
        sy+=2
        for elem in list(selected.elements.values())[:4]:
            if sy+tiny.get_height()>p1.bottom-6: break
            if elem.category.upper()=="EQUIPMENT":
                elem.ensure_item_states()
                states={}
                for st in elem.item_states:
                    states[st]=states.get(st,0)+1
                suffix=" ".join(f"{k}:{v}" for k,v in states.items() if v)
                s=f"{elem.name}: {elem.count}/{elem.initial_count}  {suffix}"
            else:
                s=f"{elem.name}: {elem.count}/{elem.initial_count}"
            screen.blit(tiny.render(_clip_text(tiny,s,p1.w-24),True,MUTED),(p1.left+16,sy)); sy+=18
    else:
        screen.blit(title.render("NO UNIT SELECTED",True,MUTED),(p1.left+18,p1.top+17)); screen.blit(small.render("Click a NATO symbol on the map.",True,MUTED),(p1.left+18,p1.top+58))

    panel_title(screen, body, pygame.Rect(p2.left,p2.top,p2.w,42), "TACTICAL STATUS / ROE")
    x=p2.left+14; sy=p2.top+55
    vals=[("SIM TIME",f"T+ {sim.time:7.1f} s"),("SPEED",f"x{sim.speed:g}"),("ENGAGEMENTS",str(len(sim.engagements))),("LAND TARGETS","ENGAGE WITHIN RANGE"),("POSTURE",selected.state.value if selected else "-"),("TARGET",selected.target_id if selected and selected.target_id else "-")]
    if selected:
        vals.append(("ACTION",str(selected.metadata.get("tactical_reason","-") or "-")))
        if getattr(sim,"stress",None) is not None and sim.stress.enabled:
            dig=sim.dig_in_fraction(selected) if hasattr(sim,"dig_in_fraction") else 0.0
            vals.append(("MORALE",f"{sim.stress.morale_state(selected)} {selected.morale*100:.0f}%   SUPPRESSION {selected.suppression*100:.0f}%"
                                   +(f"   DUG-IN {dig*100:.0f}%" if dig>0 else "")))
        if selected.branch=="ARTILLERY":
            vals.append(("FIRE PROFILE",str(selected.metadata.get("artillery_fire_profile","CONCENTRATED"))))
        if selected.metadata.get("last_contact_id"):
            age=max(0.0,sim.time-float(selected.metadata.get("last_contact_t",sim.time)))
            vals.append(("LAST CONTACT",f"{selected.metadata.get('last_contact_id')}  {age:.0f}s ago"))
    label_w=min(145,max(95,int(p2.w*.36)))
    for k,v in vals:
        if sy+26>p2.bottom-5: break
        screen.blit(small.render(k,True,MUTED),(x,sy)); box=pygame.Rect(x+label_w,sy-3,max(70,p2.w-label_w-30),25); pygame.draw.rect(screen,PANEL_3,box)
        screen.blit(small.render(_clip_text(small,v,box.w-12),True,TEXT),(box.x+8,sy)); sy+=29

    panel_title(screen, body, pygame.Rect(p3.left,p3.top,p3.w,42), "WEAPONS AND SENSORS")
    sy=p3.top+55
    if selected:
        vr,vf,va=_visual_profile_for_ui(selected,sim)
        screen.blit(small.render(_clip_text(small,f"Visual sector   {vr:.0f} m / {vf:.0f} deg",p3.w-28),True,TEXT),(p3.left+14,sy)); sy+=24
        screen.blit(small.render(_clip_text(small,f"All-round awareness   {va:.0f} m",p3.w-28),True,TEXT),(p3.left+14,sy)); sy+=24
        screen.blit(small.render(_clip_text(small,f"Watch direction   {selected.watch_heading_deg%360:.0f} deg",p3.w-28),True,TEXT),(p3.left+14,sy)); sy+=24
        for elem in selected.elements.values():
            if elem.role.upper() == "COUNTER_BATTERY_RADAR" and sy+22 < p3.bottom:
                rr=float(elem.metadata.get("radar_range_m",0.0)); status="ON" if sim.radar_element_operational(selected,elem) else "DISABLED"
                s=f"Counter-battery radar   {rr:.0f} m  {status}"; screen.blit(small.render(_clip_text(small,s,p3.w-28),True,TEXT if elem.operational else MUTED),(p3.left+14,sy)); sy+=24
        rows=[(w.name,w.range_m,elem.count,w.ammo_remaining) for elem,w in selected.operational_weapons()]
        if not rows: screen.blit(small.render("No operational weapon capability",True,MUTED),(p3.left+14,sy))
        for name,rng,cnt,ammo in rows[:6]:
            if sy+22 > p3.bottom-4: break
            ammo_txt="" if ammo < 0 else f" A:{ammo}"
            screen.blit(small.render(_clip_text(small,name,p3.w-145),True,TEXT),(p3.left+14,sy))
            screen.blit(tiny.render(f"{rng:.0f}m x{cnt}{ammo_txt}",True,MUTED),(p3.right-125,sy+2)); sy+=26
    else:
        screen.blit(small.render("Select a unit to inspect capabilities.",True,MUTED),(p3.left+14,sy))

    panel_title(screen, body, pygame.Rect(p4.left,p4.top,p4.w,42), "COMMUNICATION / EVENT LOG")
    sy=p4.top+55
    for rec in sim.logs[-10:][::-1]:
        if sy+tiny.get_height()>p4.bottom-5: break
        if rec.get("kind")=="FIRE": msg=f'{rec.get("shooter","")} -> {rec.get("target","")}  {rec.get("mode","")}'
        elif rec.get("kind")=="CB_RADAR_DETECT": msg=f'CBR {rec.get("radar","")} detects fire origin ±{rec.get("position_error_m",0):.0f}m'
        elif rec.get("kind")=="CB_RADAR_MISS": msg=f'CBR {rec.get("radar","")} no solution'
        else: msg=rec.get("kind","")
        line=f'{rec.get("t",0):6.1f}  {msg}'; screen.blit(tiny.render(_clip_text(tiny,line,p4.w-24),True,MUTED),(p4.left+12,sy)); sy+=20


def nearest_unit(sim, cam, mouse, view_mode, layout):
    if not layout.map_rect.collidepoint(mouse): return None
    best=None; bd=32
    for u in sim.units.values():
        if not u.active: continue
        if view_mode != "GOD" and u.side.value != view_mode: continue
        anchor,_true_xy=_unit_symbol_anchor(sim,cam,u,layout)
        d=math.dist(anchor,mouse)
        if d<bd: best,bd=u,d
    return best


def draw_help(screen, fonts, layout):
    """Modal F1 help overlay with the same semantic symbols used on range rings."""
    title, body, small, tiny = fonts
    shade=pygame.Surface((layout.width,layout.height),pygame.SRCALPHA); shade.fill((0,0,0,155)); screen.blit(shade,(0,0))
    w=min(900,layout.width-50); h=min(610,layout.height-40)
    r=pygame.Rect((layout.width-w)//2,(layout.height-h)//2,w,h)
    pygame.draw.rect(screen,(30,36,40),r,border_radius=8); pygame.draw.rect(screen,(103,114,120),r,1,border_radius=8)
    screen.blit(title.render("TACTICAL VIEW HELP",True,TEXT),(r.left+24,r.top+18))
    screen.blit(small.render("F1 or ESC: close",True,MUTED),(r.right-145,r.top+24))

    gap=28; col_w=(r.w-72-gap)//2
    lx=r.left+24; rx=lx+col_w+gap

    # Left column: controls and terrain.
    y=r.top+66
    screen.blit(body.render("Map navigation",True,TEXT),(lx,y)); y+=30
    for text in [
        "Mouse wheel        Zoom around mouse cursor",
        "Left-button drag   Pan; short click selects a unit",
        "HOME               Reset to complete battlefield",
        "Window resize      Tactical UI reflows automatically",
        "Grid               500 m x 500 m, equal X/Y scale",
    ]:
        screen.blit(tiny.render(text,True,MUTED),(lx+12,y)); y+=21

    y+=13; screen.blit(body.render("Simulation / view controls",True,TEXT),(lx,y)); y+=30
    for text in [
        "SPACE pause/resume     speed buttons: 1x/2x/4x/8x/16x/32x",
        "[ / ] slower/faster     1/2/4/8 direct shortcuts",
        "B BLUE FoW     R RED FoW     G ground truth",
        "L save replay log",
    ]:
        screen.blit(tiny.render(text,True,MUTED),(lx+12,y)); y+=22

    y+=13; screen.blit(body.render("Terrain / mobility",True,TEXT),(lx,y)); y+=30
    for text in [
        "ROAD: wheeled/towed gains most speed; foot little",
        "RIVER: blocked without water-crossing capability",
        "BRIDGE: passable while intact; artillery can cumulatively destroy it",
        "WOODS/BRUSH/URBAN: polygon areas can modify movement/observation",
        "RIVER may be polygon or bent polyline + width; bridges may be angled",
    ]:
        screen.blit(tiny.render(text,True,MUTED),(lx+12,y)); y+=21

    y+=13; screen.blit(body.render("Fog-of-war / artillery marks",True,TEXT),(lx,y)); y+=29
    for color,label in [
        ((115,105,96),"Contact position-uncertainty circle"),
        (YELLOW,"Selected unit / objective highlight"),
        ((205,135,45),"Recent artillery aim CEP / solution ring"),
        ((185,85,45),"Recent artillery impact / effect-radius ring"),
    ]:
        pygame.draw.line(screen,color,(lx+12,y+7),(lx+48,y+7),3)
        screen.blit(tiny.render(label,True,TEXT),(lx+58,y)); y+=22

    # Right column: selected-unit rings.
    y=r.top+66
    screen.blit(body.render("Selected-unit range overlays",True,TEXT),(rx,y)); y+=30
    screen.blit(tiny.render("Each visible ring shows icon + short weapon/sensor label + range.",True,MUTED),(rx+12,y)); y+=25
    range_legend=[
        (CYAN,"EYE","Visual: directional long-range sector + short 360 awareness"),
        (PURPLE,"RADAR","Operational counter-battery radar envelope"),
        (ORANGE,"BULLET","Small arms / machine-gun effective range"),
        (GUN_RED,"CANNON","Direct-fire cannon / tank-gun range"),
        (MAGENTA,"MISSILE","Guided anti-armor / missile range"),
        (ARTY_GOLD,"SHELL","Indirect-fire / artillery range"),
    ]
    for color,kind,label in range_legend:
        pygame.draw.line(screen,color,(rx+12,y+9),(rx+46,y+9),2)
        _draw_range_symbol(screen,(rx+56,y+9),kind,color)
        screen.blit(tiny.render(label,True,TEXT),(rx+73,y+1)); y+=27

    y+=8
    screen.blit(body.render("Symbol meaning",True,TEXT),(rx,y)); y+=30
    for kind,color,label in [
        ("EYE",CYAN,"eye = visual / ordinary sensing"),
        ("RADAR",PURPLE,"radar mast = counter-battery sensor"),
        ("BULLET",ORANGE,"bullet = small arms / machine gun"),
        ("CANNON",GUN_RED,"cannon round = direct-fire gun / tank gun"),
        ("MISSILE",MAGENTA,"missile = guided anti-armor weapon"),
        ("SHELL",ARTY_GOLD,"shell = indirect-fire / artillery weapon"),
    ]:
        _draw_range_symbol(screen,(rx+22,y+8),kind,color)
        screen.blit(tiny.render(label,True,MUTED),(rx+40,y)); y+=24

    y+=7
    for text in [
        "Visual sensing = long directional sector + short 360-degree awareness circle.",
        "Watch direction follows movement/known threat/objective; radar remains 360-degree.",
        "Sector edge is a maximum envelope, not guaranteed sighting.",
        "Weapon rings include only currently operational weapon capabilities.",
        "Ring color + icon identify a fixed semantic family, never range order.",
        "Labels distinguish same-family ranges, e.g. SA 350m / DMR 500m.",
        "Dense FOREST: foot movement slow; tracked/wheeled off-road blocked; visual penetration ~100m.",
        "Dynamic grid spacing and upper-left scale bar show true metric distance.",
    ]:
        screen.blit(tiny.render(text,True,MUTED),(rx+12,y)); y+=20


def _choose_json_file(title, initial_dir="scenarios"):
    """Native JSON picker kept outside the simulation kernel."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root=tk.Tk(); root.withdraw(); root.attributes("-topmost",True)
        path=filedialog.askopenfilename(title=title,initialdir=os.path.abspath(initial_dir),filetypes=[("JSON","*.json"),("All files","*.*")])
        root.destroy(); return path or None
    except Exception:
        return None

def _choose_scenario_file(initial_dir="scenarios"):
    return _choose_json_file("Select Scenario (battlefield / OOB)", initial_dir)

def _choose_bml_file(side, initial_dir="scenarios"):
    return _choose_json_file(f"Select {side} BML plan (Cancel = no {side} BML)", initial_dir)

def _bml_selection_dict(blue_path=None, red_path=None):
    out={}
    if blue_path: out["BLUE"]=blue_path
    if red_path: out["RED"]=red_path
    return out

def _choose_run_files(initial_dir="scenarios"):
    scenario_path=_choose_scenario_file(initial_dir)
    if not scenario_path:
        return None, {}
    base=os.path.dirname(scenario_path) or initial_dir
    blue=_choose_bml_file("BLUE", base)
    red=_choose_bml_file("RED", base)
    return scenario_path, _bml_selection_dict(blue, red)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("scenario",nargs="?",default=None,help="scenario JSON (battlefield/OOB only); if omitted GUI file pickers are used")
    ap.add_argument("--blue-bml",default=None,help="optional BLUE BML mission JSON")
    ap.add_argument("--red-bml",default=None,help="optional RED BML mission JSON")
    ap.add_argument("--no-bml",action="store_true",help="run scenario without external BLUE/RED BML plans")
    args=ap.parse_args()

    # Scenario and BML plans are deliberately independent.  GUI launch asks for the scenario,
    # then optional BLUE and RED plans.  Explicit CLI paths never trigger additional dialogs.
    if args.scenario:
        scenario_path=args.scenario
        # Supplying only a scenario path should not silently disable BML. If no explicit
        # BML CLI options were supplied, ask for BLUE/RED plans exactly as the GUI-only
        # launch does. Use --no-bml when HOLD/default scenario orders are intentional.
        if args.no_bml:
            selected_bml={}
        elif args.blue_bml or args.red_bml:
            selected_bml=_bml_selection_dict(args.blue_bml,args.red_bml)
        else:
            base=os.path.dirname(os.path.abspath(scenario_path)) or "scenarios"
            blue=_choose_bml_file("BLUE",base)
            red=_choose_bml_file("RED",base)
            selected_bml=_bml_selection_dict(blue,red)
    else:
        if args.no_bml:
            scenario_path=_choose_scenario_file("scenarios")
            selected_bml={}
        else:
            scenario_path, selected_bml=_choose_run_files("scenarios")
    if not scenario_path:
        print("No scenario selected; simulator not started.")
        return
    try:
        sim=load_scenario(scenario_path,bml_files=selected_bml)
    except Exception as ex:
        # Untrusted scenario/BML data: report the offending field instead of a traceback.
        print(f"Failed to load scenario {scenario_path}: {ex}")
        return
    set_world(sim)
    controller=SimulationController(sim, supported_speeds=SIM_SPEEDS)
    print(f"Scenario: {os.path.abspath(scenario_path)}")
    print(f"Terrain : {getattr(sim, 'terrain_file', '(none)')}")
    print(f"BLUE BML: {sim.bml_files.get('BLUE', '(none)')}")
    print(f"RED BML : {sim.bml_files.get('RED', '(none)')}")

    pygame.init(); pygame.display.set_caption("MNS / COA Tactical Command View")
    screen=pygame.display.set_mode((DEFAULT_W,DEFAULT_H),pygame.RESIZABLE)
    clock=pygame.time.Clock()
    fonts=(pygame.font.SysFont("arial",26),pygame.font.SysFont("arial",17,bold=True),pygame.font.SysFont("consolas",14),pygame.font.SysFont("consolas",12))
    map_font=pygame.font.SysFont("arial",13,bold=True); map_tiny=pygame.font.SysFont("consolas",11)

    layout=build_layout(*screen.get_size())
    cam=Camera(WORLD_W/2,WORLD_H/2,1.0)
    selected=None; view_mode="BLUE"; dragging=False; drag_origin=(0,0); last_mouse=(0,0); drag_moved=False; running=True; help_visible=False
    sim_error=None

    while running:
        dt=min(clock.tick(FPS)/1000.0,0.05)
        for e in pygame.event.get():
            if e.type==pygame.QUIT: running=False
            elif e.type==pygame.VIDEORESIZE:
                new_w=max(MIN_W,e.w); new_h=max(MIN_H,e.h)
                screen=pygame.display.set_mode((new_w,new_h),pygame.RESIZABLE)
                layout=build_layout(new_w,new_h); cam.clamp(layout.map_rect)
            elif e.type==pygame.MOUSEWHEEL and not help_visible:
                pos=pygame.mouse.get_pos()
                if layout.map_rect.collidepoint(pos):
                    before=cam.screen_to_world(pos,layout.map_rect)
                    cam.zoom=max(1.0,min(12.0,cam.zoom*(1.20 if e.y>0 else 1/1.20)))
                    after=cam.screen_to_world(pos,layout.map_rect)
                    cam.cx += before[0]-after[0]; cam.cy += before[1]-after[1]; cam.clamp(layout.map_rect)
            elif e.type==pygame.MOUSEBUTTONDOWN and not help_visible:
                if e.button==1 and layout.map_rect.collidepoint(e.pos):
                    dragging=True; drag_origin=e.pos; last_mouse=e.pos; drag_moved=False
            elif e.type==pygame.MOUSEBUTTONUP and e.button==1:
                speed_hit=False
                if not help_visible:
                    for val,rect in speed_button_rects(layout):
                        if rect.collidepoint(e.pos):
                            controller.set_speed(val); speed_hit=True; break
                if dragging and not drag_moved and layout.map_rect.collidepoint(e.pos) and not speed_hit:
                    selected=nearest_unit(sim,cam,e.pos,view_mode,layout)
                dragging=False
            elif e.type==pygame.MOUSEMOTION and dragging and not help_visible:
                if math.hypot(e.pos[0]-drag_origin[0],e.pos[1]-drag_origin[1]) >= 5:
                    drag_moved=True
                if drag_moved:
                    scale=max(cam.scale(layout.map_rect),1e-9); dx=e.pos[0]-last_mouse[0]; dy=e.pos[1]-last_mouse[1]
                    cam.cx -= dx/scale; cam.cy -= dy/scale; cam.clamp(layout.map_rect)
                last_mouse=e.pos
            elif e.type==pygame.KEYDOWN:
                if (e.mod & pygame.KMOD_CTRL) and e.key==pygame.K_o:
                    chosen, chosen_bml=_choose_run_files(os.path.dirname(scenario_path) or "scenarios")
                    if chosen:
                        try:
                            sim=load_scenario(chosen,bml_files=chosen_bml); scenario_path=chosen; selected_bml=chosen_bml; set_world(sim)
                            controller=SimulationController(sim, supported_speeds=SIM_SPEEDS)
                            cam=Camera(WORLD_W/2,WORLD_H/2,1.0); selected=None; sim_error=None
                        except Exception as ex:
                            print(f"Failed to load scenario {chosen}: {ex}")
                elif e.key==pygame.K_F1: help_visible=not help_visible; dragging=False
                elif e.key==pygame.K_ESCAPE and help_visible: help_visible=False
                elif not help_visible:
                    if e.key==pygame.K_SPACE: controller.toggle_pause()
                    elif e.key==pygame.K_1: controller.set_speed(1)
                    elif e.key==pygame.K_2: controller.set_speed(2)
                    elif e.key==pygame.K_4: controller.set_speed(4)
                    elif e.key==pygame.K_8: controller.set_speed(8)
                    elif e.key==pygame.K_LEFTBRACKET: controller.step_speed(-1)
                    elif e.key==pygame.K_RIGHTBRACKET: controller.step_speed(1)
                    elif e.key==pygame.K_b: view_mode="BLUE"; selected=None
                    elif e.key==pygame.K_r: view_mode="RED"; selected=None
                    elif e.key==pygame.K_g: view_mode="GOD"; selected=None
                    elif e.key==pygame.K_HOME: cam.reset()
                    elif e.key==pygame.K_l:
                        out=_replay_log_path(); sim.save_log(out); print(f"saved {out}")

        # F1 behaves as a modal inspection popup: simulation is visually frozen while open.
        # At high acceleration, split wall-clock advancement into bounded simulation-time
        # substeps so sensor scans and chained discrete events do not become frame-rate dependent.
        if not help_visible and sim_error is None:
            try:
                controller.advance_realtime(dt)
            except Exception as ex:
                # Keep the operator picture alive: pause on an engine error rather than exiting.
                import traceback
                traceback.print_exc()
                sim.paused=True
                sim_error=f"SIMULATION HALTED at T={sim.time:.1f}s: {type(ex).__name__}: {ex}"

        screen.fill(PANEL)
        # The engine advances in fixed 0.25 s steps; draw units between the last two steps so
        # 1x/2x motion stays smooth.  Positions are restored right after drawing.
        _saved_pos=_apply_display_positions(sim)
        try:
            draw_terrain(screen,cam,map_tiny,layout,sim)
            draw_orders_and_engagements(screen,cam,sim,view_mode,layout)
            draw_objectives(screen,cam,sim,map_font,layout)
            draw_recent_artillery(screen,cam,sim,layout)
            draw_ranges(screen,cam,selected,layout,fonts[3],sim)
            if view_mode == "GOD":
                for u in sim.units.values():
                    if u.active: draw_nato_symbol(screen,cam,map_font,map_tiny,sim,u,selected is u,layout)
            else:
                viewer=Side(view_mode)
                for u in sim.units.values():
                    if u.active and u.side == viewer: draw_nato_symbol(screen,cam,map_font,map_tiny,sim,u,selected is u,layout)
                for tr in sim.side_tracks(viewer).values(): draw_contact(screen,cam,map_font,map_tiny,tr,sim,viewer,layout)
            draw_map_buttons(screen,map_tiny,sim,cam,layout)
            badge=map_tiny.render(f"VIEW {view_mode}   [B] BLUE  [R] RED  [G] GOD   [F1] HELP",True,(45,48,45)); screen.blit(badge,(layout.map_rect.left+12,layout.map_rect.top+12))
            draw_bottom(screen,fonts,sim,selected,layout)
        finally:
            _restore_positions(sim,_saved_pos)

        bml_b=os.path.basename(sim.bml_files.get("BLUE","-")) if getattr(sim,"bml_files",None) else "-"
        bml_r=os.path.basename(sim.bml_files.get("RED","-")) if getattr(sim,"bml_files",None) else "-"
        status_text = f"AREA {WORLD_W/1000:g}x{WORLD_H/1000:g} km | BML B:{bml_b} R:{bml_r} | wheel zoom | HOME reset | F1 help"
        status=map_tiny.render(_clip_text(map_tiny,status_text,max(100,layout.map_rect.w-450)),True,(77,80,74))
        screen.blit(status,(min(layout.map_rect.left+440,layout.map_rect.right-status.get_width()-8),layout.map_rect.bottom-38))

        if sim_error:
            banner=map_tiny.render(_clip_text(map_tiny,sim_error+"  (Ctrl+O to load another scenario)",max(100,layout.map_rect.w-24)),True,(255,255,255))
            pygame.draw.rect(screen,(170,40,40),(layout.map_rect.left+8,layout.map_rect.top+34,banner.get_width()+12,banner.get_height()+8))
            screen.blit(banner,(layout.map_rect.left+14,layout.map_rect.top+38))
        if help_visible: draw_help(screen,fonts,layout)
        pygame.display.flip()

    pygame.quit()


def _apply_display_positions(sim):
    alpha=sim.realtime_alpha() if hasattr(sim,"realtime_alpha") else 1.0
    saved={}
    for u in sim.units.values():
        shown=sim.display_position(u,alpha) if hasattr(sim,"display_position") else u.pos
        if shown!=u.pos:
            saved[u.uid]=u.pos; u.pos=shown
    return saved


def _restore_positions(sim,saved):
    for uid,pos in saved.items():
        sim.units[uid].pos=pos


def _replay_log_path(directory="logs"):
    """Unique, timestamped replay file so an earlier run is never silently overwritten."""
    import datetime
    os.makedirs(directory,exist_ok=True)
    stamp=datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path=os.path.join(directory,f"replay-{stamp}.jsonl"); n=1
    while os.path.exists(path):
        n+=1; path=os.path.join(directory,f"replay-{stamp}-{n}.jsonl")
    return path


if __name__=="__main__":
    main()
