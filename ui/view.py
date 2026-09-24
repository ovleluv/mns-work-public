"""Map world size, responsive screen layout and camera of the Pygame tactical UI."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple
import pygame
from .theme import MIN_H, MIN_W


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
