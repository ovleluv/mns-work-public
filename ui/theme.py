"""Window sizes and colour palette of the Pygame tactical UI."""
from __future__ import annotations


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
