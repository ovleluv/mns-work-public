from __future__ import annotations
import math
from typing import Iterable, Sequence, Tuple

Pt = Tuple[float, float]
Rect = Tuple[float, float, float, float]


def _overlap(a: Rect, b: Rect, pad: float = 0.0) -> bool:
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    return not (ax + aw + pad <= bx or bx + bw + pad <= ax or ay + ah + pad <= by or by + bh + pad <= ay)


def contour_label_candidate(points: Sequence[Pt], label_size: Tuple[float, float], occupied: Iterable[Rect] = (), padding_px: float = 5.0):
    """Choose a readable non-overlapping label anchor on a polygon contour.

    Candidates are segment midpoints, longest first.  The text follows the local tangent, but its
    angle is normalized to the readable [-90,+90] range.  The returned rectangle is a conservative
    axis-aligned bounding box for collision tests; renderers may use the same center/angle to rotate
    the actual text surface.
    """
    if len(points) < 2:
        return None
    w, h = map(float, label_size)
    candidates = []
    for i in range(len(points)):
        a = points[i]; b = points[(i + 1) % len(points)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length < max(10.0, w * 0.75):
            continue
        angle = math.degrees(math.atan2(dy, dx))
        while angle > 90.0: angle -= 180.0
        while angle < -90.0: angle += 180.0
        rad = math.radians(angle)
        rw = abs(w * math.cos(rad)) + abs(h * math.sin(rad))
        rh = abs(w * math.sin(rad)) + abs(h * math.cos(rad))
        mid = ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)
        rect = (mid[0] - rw * 0.5, mid[1] - rh * 0.5, rw, rh)
        candidates.append((length, mid, angle, rect))
    candidates.sort(key=lambda x: x[0], reverse=True)
    occ = list(occupied)
    for _, mid, angle, rect in candidates:
        if not any(_overlap(rect, r, padding_px) for r in occ):
            return mid, angle, rect
    return None
