"""Pygame tactical UI entry point: argument handling, scenario/BML selection and the main loop.

Responsive CMO-inspired tactical UI.  Drawing lives in the ``ui`` package; the simulation
kernel in ``mnsim`` remains independent from pygame.
"""
from __future__ import annotations
import argparse
import math
import os

import pygame

from mnsim.scenario import load_scenario
from mnsim.model import Side
from mnsim.application import SimulationController

from ui.view import Camera, build_layout, set_world
from ui import view
from ui.theme import DEFAULT_H, DEFAULT_W, FPS, MIN_H, MIN_W, PANEL
from ui.panels import SIM_SPEEDS, draw_bottom, draw_help, draw_map_buttons, nearest_unit, speed_button_rects
from ui.dialogs import _bml_selection_dict, _choose_bml_file, _choose_run_files, _choose_scenario_file
from ui.render import (
    _clip_text, draw_contact, draw_nato_symbol, draw_objectives, draw_orders_and_engagements, draw_ranges,
    draw_recent_artillery, draw_terrain,
)


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
    cam=Camera(view.WORLD_W/2,view.WORLD_H/2,1.0)
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
                            cam=Camera(view.WORLD_W/2,view.WORLD_H/2,1.0); selected=None; sim_error=None
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
        status_text = f"AREA {view.WORLD_W/1000:g}x{view.WORLD_H/1000:g} km | BML B:{bml_b} R:{bml_r} | wheel zoom | HOME reset | F1 help"
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
