"""Boundary events for point movement through polygon and polyline terrain.

Polyline widths use the same union of capsules (segment plus round caps) as
TerrainModel's point predicates. Extra events are harmless; missing a boundary
would allow a narrow forbidden interval to disappear between samples.
"""
import math


BOUNDARY_EPS_M = 1e-8


def polygon_cuts(a, b, polygon):
    dx, dy = b[0]-a[0], b[1]-a[1]
    length2 = dx*dx + dy*dy
    if not length2:
        return
    for c, d in zip(polygon, polygon[1:] + polygon[:1]):
        ex, ey = d[0]-c[0], d[1]-c[1]
        qx, qy = c[0]-a[0], c[1]-a[1]
        denominator = dx*ey - dy*ex
        if denominator != 0.0:
            t = (qx*ey-qy*ex)/denominator
            u = (qx*dy-qy*dx)/denominator
            if 0 <= t <= 1 and 0 <= u <= 1:
                yield t
        elif abs(qx*dy-qy*dx) <= BOUNDARY_EPS_M * math.sqrt(length2):
            # Collinear overlaps still have endpoints where membership changes.
            for p in (c, d):
                t = ((p[0]-a[0])*dx + (p[1]-a[1])*dy)/length2
                if 0 <= t <= 1:
                    yield t


def capsule_cuts(a, b, c, d, radius):
    dx, dy = b[0]-a[0], b[1]-a[1]
    length2 = dx*dx + dy*dy
    if not length2:
        return
    ex, ey = d[0]-c[0], d[1]-c[1]
    axis_length = math.hypot(ex, ey)
    if axis_length:
        ux, uy = ex/axis_length, ey/axis_length
        along = (a[0]-c[0])*ux + (a[1]-c[1])*uy
        across = -(a[0]-c[0])*uy + (a[1]-c[1])*ux
        for origin, rate, limits in (
            (along, dx*ux+dy*uy, (0, axis_length)),
            (across, -dx*uy+dy*ux, (-radius, radius)),
        ):
            if rate:
                for limit in limits:
                    t = (limit-origin)/rate
                    if 0 <= t <= 1:
                        yield t
    for center in (c, d):
        # Projection form avoids subtracting two large squared distances when
        # a short road/river cap is intersected by a long movement segment.
        qx, qy = center[0]-a[0], center[1]-a[1]
        closest_t = (qx*dx+qy*dy)/length2
        perpendicular2 = (qx*dy-qy*dx)**2/length2
        delta2 = radius*radius-perpendicular2
        if delta2 >= 0:
            delta_t = math.sqrt(delta2/length2)
            for t in (closest_t-delta_t, closest_t+delta_t):
                if 0 <= t <= 1:
                    yield t


def movement_regions(terrain, unit):
    """Mutable terrain rules are read on each call; never cache capabilities."""
    md = unit.unit_type.metadata
    mobility = str(md.get("mobility_class", "FOOT")).upper()
    caps = {str(c).upper() for c in md.get("mobility_capabilities", [])}
    # Only potentially restrictive polygons need events. All building/lake
    # boundaries remain candidates because access/overlap rules are dynamic.
    areas = []
    for area in terrain.areas:
        kind = str(area.get("type", "")).upper()
        forbidden = {str(c).upper() for c in area.get("impassable_mobility_classes", [])}
        restricted = mobility in forbidden or float(area.get("mobility_overrides", {}).get(mobility, 1)) <= 0
        if restricted or kind in ("BUILDING", "LAKE"):
            areas.append(area)
    water_restricted = any(
        float(r["mobility_overrides"][mobility]) <= 0 if mobility in r.get("mobility_overrides", {})
        else not bool(caps & {"AMPHIBIOUS", "WATER_CROSSING"})
        for r in terrain.rivers)
    return areas, water_restricted


def movement_cuts(terrain, a, b, areas, water_restricted):
    cuts = {0.0, 1.0}
    road_exceptions = False
    for area in areas:
        poly = area.get("polygon", [])
        if len(poly) >= 3 and terrain._segment_bbox_overlap(a, b, terrain._poly_bbox(poly)):
            cuts.update(polygon_cuts(a, b, poly))
            road_exceptions = True
    if water_restricted:
        for river in terrain.rivers:
            poly = river.get("polygon", [])
            if len(poly) >= 3 and terrain._segment_bbox_overlap(a, b, terrain._poly_bbox(poly)):
                cuts.update(polygon_cuts(a, b, poly))
    lines = ([(r.get("points", []), float(r.get("width_m", 30))/2) for r in terrain.roads]
             if road_exceptions else [])
    if water_restricted:
        lines += [(r.get("points", []), float(r.get("width_m", 0))/2) for r in terrain.rivers]
        lines += [(terrain.bridge_points(r), float(r.get("width_m", 80))/2)
                  for r in terrain.bridges if terrain.bridge_operational(r)]
    for points, radius in lines:
        if radius <= 0:
            continue
        for c, d in zip(points, points[1:]):
            bbox = (min(c[0], d[0])-radius, min(c[1], d[1])-radius,
                    max(c[0], d[0])+radius, max(c[1], d[1])+radius)
            if terrain._segment_bbox_overlap(a, b, bbox):
                cuts.update(capsule_cuts(a, b, c, d, radius))
    return sorted(cuts)
