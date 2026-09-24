"""Map drawing for the Pygame tactical UI: terrain, symbols, contacts, ranges, fires and orders."""
from __future__ import annotations
import math
import pygame
from mnsim.model import Side, UnitState
from mnsim.symbology import echelon_amplifier, branch_symbol_kind
from mnsim.cartography import contour_label_candidate
from . import view
from .theme import (
    ARTY_GOLD, BLACK, BLUE, CYAN, GREEN, GRID, GUN_RED, MAGENTA, MAP_BG, MAP_BG_2, ORANGE, PURPLE, RED, ROAD,
    WATER, YELLOW,
)


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

    tl=cam.world_to_screen((0,0),mr); br=cam.world_to_screen((view.WORLD_W,view.WORLD_H),mr)
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
    while x<=view.WORLD_W+1e-6:
        a=cam.world_to_screen((x,0),mr); b=cam.world_to_screen((x,view.WORLD_H),mr)
        pygame.draw.line(screen,GRID,a,b,1)
        x+=step
    y=math.floor(top/step)*step
    while y<=view.WORLD_H+1e-6:
        a=cam.world_to_screen((0,y),mr); b=cam.world_to_screen((view.WORLD_W,y),mr)
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
