"""Native file pickers used by the Pygame tactical UI to choose a scenario and BML plans."""
from __future__ import annotations
import os


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
