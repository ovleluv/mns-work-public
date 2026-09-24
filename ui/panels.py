"""Bottom information panels, map buttons, help overlay and unit picking of the Pygame tactical UI."""
from __future__ import annotations
import math
import pygame
from mnsim.application import SimulationController, DEFAULT_SIM_SPEEDS
from mnsim.mounted import transport_capacity, free_seats, required_vehicle_crew, crew_available
from .theme import (
    ARTY_GOLD, CYAN, GUN_RED, MAGENTA, MUTED, ORANGE, PANEL, PANEL_2, PANEL_3, PURPLE, TEXT, YELLOW,
)
from .render import _clip_text, _draw_range_symbol, _unit_symbol_anchor, _visual_profile_for_ui, side_color


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
