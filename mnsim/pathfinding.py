from __future__ import annotations

from .mobility import movement_speed_mps

import heapq
import math
from dataclasses import dataclass
from typing import Dict, List, Tuple

Vec2 = Tuple[float, float]


@dataclass(frozen=True)
class NavNode:
    key: str
    pos: Vec2
    kind: str = "GENERIC"


class NavigationPlanner:
    """Sparse visibility-graph A* planner for formation-level tactical movement.

    The planner intentionally avoids a dense game-style navigation grid.  Current maps are
    kilometer-scale vector terrain, so road vertices, bridge portals, the current position and
    the destination form a compact graph.  Edges are allowed only when the complete segment is
    physically passable for the formation.  Edge cost is estimated travel time using the
    formation's terrain mobility factors, so a wheeled formation may prefer a longer road route
    while tracked units are more willing to cut across open terrain.

    Risk/threat/mine/cover costs are deliberately not decided here yet.  They can later be added
    through a route-cost policy supplied by doctrine/COA without changing movement or terrain APIs.
    """

    def __init__(self, terrain, config: Dict | None = None):
        self.terrain = terrain
        self.config = dict(config or {})

    @property
    def sample_spacing_m(self) -> float:
        return max(4.0, float(self.config.get("sample_spacing_m", 12.0)))

    def _bridge_nodes(self) -> List[NavNode]:
        out: List[NavNode] = []
        margin = float(self.config.get("bridge_portal_margin_m", 8.0))
        for br in self.terrain.bridges:
            if not self.terrain.bridge_operational(br):
                continue
            bid = str(br.get("id", "BR"))
            pts=self.terrain.bridge_points(br)
            if len(pts)<2: continue
            a,b=pts[0],pts[-1]
            # Extend portals just beyond each authored end along its local tangent.
            def extend(p0,p1,amount):
                dx,dy=p0[0]-p1[0],p0[1]-p1[1]; d=max(math.hypot(dx,dy),1e-9)
                return (p0[0]+dx/d*amount,p0[1]+dy/d*amount)
            pa=extend(a,pts[1],margin); pb=extend(b,pts[-2],margin)
            out.append(NavNode(f"BR:{bid}:A",pa,"BRIDGE_PORTAL"))
            for i,p in enumerate(pts):out.append(NavNode(f"BR:{bid}:C{i}",p,"BRIDGE"))
            out.append(NavNode(f"BR:{bid}:B",pb,"BRIDGE_PORTAL"))
        return out

    def _barricade_nodes(self) -> List[NavNode]:
        out=[]
        margin=float(self.config.get("barricade_endpoint_margin_m",3.0))
        for b in getattr(self.terrain,"barricades",[]):
            bid=str(b.get("id","HESCO")); a,z=self.terrain.barricade_endpoints(b)
            dx,dy=z[0]-a[0],z[1]-a[1]; d=max(math.hypot(dx,dy),1e-9); ux,uy=dx/d,dy/d
            out.append(NavNode(f"BAR:{bid}:A",(a[0]-ux*margin,a[1]-uy*margin),"BARRICADE_END"))
            out.append(NavNode(f"BAR:{bid}:B",(z[0]+ux*margin,z[1]+uy*margin),"BARRICADE_END"))
        return out


    def _building_nodes(self) -> List[NavNode]:
        """Visibility-graph portals around operational building corners.

        Buildings are hard obstacles for ordinary movement, so the sparse planner needs corner
        candidates to route around them rather than declaring every blocked direct leg unreachable.
        """
        out=[]; margin=float(self.config.get("building_corner_margin_m",4.0))
        for b in getattr(self.terrain,"areas",[]):
            if str(b.get("type","")).upper()!="BUILDING" or not self.terrain.building_operational(b):continue
            poly=[(float(p[0]),float(p[1])) for p in b.get("polygon",[])]
            if len(poly)<3:continue
            cx=sum(x for x,y in poly)/len(poly); cy=sum(y for x,y in poly)/len(poly); bid=str(b.get("id","BLD"))
            for i,(x,y) in enumerate(poly):
                dx,dy=x-cx,y-cy; d=max(math.hypot(dx,dy),1e-9)
                out.append(NavNode(f"BLD:{bid}:{i}",(x+dx/d*margin,y+dy/d*margin),"BUILDING_CORNER"))
        return out

    def _road_nodes(self) -> List[NavNode]:
        out: List[NavNode] = []
        for road in self.terrain.roads:
            rid = str(road.get("id", "ROAD"))
            for i, p in enumerate(road.get("points", [])):
                out.append(NavNode(f"ROAD:{rid}:{i}", (float(p[0]), float(p[1])), "ROAD"))
        return out

    def _area_nodes(self, unit) -> List[NavNode]:
        """Fallback portals around impassable polygons, including concave maps.

        Use incident-edge outward normals, not a centroid that may be outside
        a concave polygon. Validate every candidate against overlapping terrain.
        Road/bridge graphs are tried first to avoid expanding every routine plan.
        """
        out=[]
        mobility=str(unit.unit_type.metadata.get("mobility_class","FOOT")).upper()
        margin=max(4.0,float(self.config.get("area_corner_margin_m",4.0)))
        for area in self.terrain.areas:
            kind=str(area.get("type","")).upper()
            forbidden={str(x).upper() for x in area.get("impassable_mobility_classes",[])}
            override=area.get("mobility_overrides",{}).get(mobility,1)
            if kind in ("BUILDING","ELEVATION"):continue
            if kind!="LAKE" and mobility not in forbidden and float(override)>0:continue
            poly=area.get("polygon",[])
            if len(poly)<3:continue
            winding=1 if sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(poly,poly[1:]+poly[:1]))>0 else -1
            for i,p in enumerate(poly):
                prev,nxt=poly[i-1],poly[(i+1)%len(poly)]
                normals=[]
                for a,b in ((prev,p),(p,nxt)):
                    dx,dy=b[0]-a[0],b[1]-a[1]; length=math.hypot(dx,dy)
                    if length:normals.append((winding*dy/length,-winding*dx/length))
                nx,ny=sum(n[0] for n in normals),sum(n[1] for n in normals)
                length=math.hypot(nx,ny)
                if not length:continue
                q=(p[0]+margin*nx/length,p[1]+margin*ny/length)
                world=getattr(self.terrain,"world",None)
                if world and not (0<=q[0]<=world["width_m"] and 0<=q[1]<=world["height_m"]):continue
                if self.terrain.passable(unit,q):
                    out.append(NavNode(f"AREA:{area.get('id','AREA')}:{i}",q,"AREA_CORNER"))
        return out

    @staticmethod
    def _dedupe(nodes: List[NavNode]) -> List[NavNode]:
        # Keep distinct semantic nodes unless their positions are virtually identical; a sparse
        # graph does not benefit from overlapping road-junction duplicates.
        seen = {}
        out = []
        for n in nodes:
            q = (round(n.pos[0], 3), round(n.pos[1], 3))
            if n.kind in ("START", "DEST") or q not in seen:
                seen[q] = n
                out.append(n)
        return out

    #: Upper bound on samples per segment; very long legs get proportionally coarser sampling
    #: instead of allocating an unbounded list (e.g. a BML destination at 1e13 m).
    MAX_SEGMENT_SAMPLES = 2000

    def _segment_samples(self, a: Vec2, b: Vec2):
        d = math.dist(a, b)
        if not math.isfinite(d):
            raise ValueError(f"non-finite navigation segment {a!r} -> {b!r}")
        n = max(1, min(self.MAX_SEGMENT_SAMPLES, int(math.ceil(d / self.sample_spacing_m))))
        for i in range(n + 1):
            t = i / n
            yield (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    def _max_grade_deg(self, unit) -> float:
        mobility=str(unit.unit_type.metadata.get("mobility_class","FOOT")).upper()
        table={"FOOT":38.0,"TRACKED":28.0,"WHEELED":18.0,"WHEELED_TOWED":12.0}
        table.update({str(k).upper():float(v) for k,v in dict(self.config.get("max_grade_deg_by_mobility",{})).items()})
        return float(table.get(mobility,self.config.get("default_max_grade_deg",20.0)))

    @staticmethod
    def _unit_type_key(unit):
        # Unit types are shared per template; detached/aggregated units get their own object.
        return id(unit.unit_type)

    def _cache(self):
        rev=self.terrain.revision()
        if getattr(self,"_cache_rev",None)!=rev or len(getattr(self,"_edge_cache",{}))>200_000:
            self._cache_rev=rev
            self._edge_cache={}
        return self._edge_cache

    def _terrain_segment_ok(self, md, a: Vec2, b: Vec2) -> bool:
        """Exact test that the surface speed factor stays positive along a-b.

        The factor only changes where the segment crosses an area/road/bridge/river boundary,
        so one probe per boundary interval replaces fixed-spacing sampling.
        """
        cuts=self.terrain.speed_boundary_cuts(a,b)
        for lo,hi in zip(cuts,cuts[1:]):
            if hi-lo<=1e-12:
                continue
            m=(lo+hi)*0.5
            q=(a[0]+(b[0]-a[0])*m,a[1]+(b[1]-a[1])*m)
            if self.terrain.terrain_speed_factor(md,q)<=0.0:
                return False
        return True

    def _grade_ok(self, unit, a: Vec2, b: Vec2) -> bool:
        if not self.terrain.has_elevation():
            return True
        max_grade=self._max_grade_deg(unit)
        samples=list(self._segment_samples(a,b))
        # Prevent routes across implausibly steep authored contour transitions.  Thresholds are
        # formation-scale planning defaults, not vehicle brochure climb limits.
        return all(abs(self.terrain.slope_angle_deg(x,y))<=max_grade for x,y in zip(samples,samples[1:]))

    def segment_passable(self, unit, a: Vec2, b: Vec2) -> bool:
        a=(float(a[0]),float(a[1])); b=(float(b[0]),float(b[1]))
        if not self.terrain.segment_passable(unit,a,b):return False
        # Unit-level mobility (crew, speed, surviving mobile vehicles) is independent of the leg.
        if movement_speed_mps(unit,None,state="MOVING")<=0 or self.terrain.mobile_equipment_fraction(unit)<=0:
            return False
        cache=self._cache()
        key=("PASS",self._unit_type_key(unit),a,b)
        hit=cache.get(key)
        if hit is None:
            md=unit.unit_type.metadata
            hit=self._terrain_segment_ok(md,a,b) and self._grade_ok(unit,a,b)
            cache[key]=hit
        return hit

    def _segment_time_cost(self, unit, a: Vec2, b: Vec2) -> float:
        d = math.dist(a, b)
        if d <= 1e-9:
            return 0.0
        cache=self._cache()
        # Everything that scales the unit's speed must be part of the key (all mutable at runtime).
        key=("COST",self._unit_type_key(unit),float(unit.unit_type.max_speed_mps),
             str(unit.echelon).upper(),bool(unit.crew_failure_reason),
             round(self.terrain.mobile_equipment_fraction(unit),6),
             (float(a[0]),float(a[1])),(float(b[0]),float(b[1])))
        if key in cache:
            return cache[key]
        samples = list(self._segment_samples(a, b))
        total = 0.0
        seg = d / max(1, len(samples) - 1)
        for i,p in enumerate(samples[:-1]):
            nxt=samples[i+1]
            speed=movement_speed_mps(unit,self.terrain,p,nxt,state="MOVING")
            if speed <= 0:
                total=math.inf
                break
            total += seg / speed
        cache[key]=total
        return total

    def _heuristic(self, unit, a: Vec2, b: Vec2) -> float:
        factors = dict(unit.unit_type.metadata.get("terrain_speed_factors", {}))
        best = max([1.0] + [float(v) for v in factors.values() if float(v) > 0.0])
        return math.dist(a, b) / max(0.05, unit.unit_type.max_speed_mps * best)

    def plan(self, unit, start: Vec2, destination: Vec2, _with_area_nodes=False) -> List[Vec2]:
        start = (float(start[0]), float(start[1]))
        destination = (float(destination[0]), float(destination[1]))
        if not self.terrain.passable(unit, destination):
            return [start]
        if start == destination:
            return [start, destination]

        # A direct route is only an early return when no useful vector-network alternative can be
        # faster.  Otherwise roads must be allowed to compete on travel time.
        nodes = [NavNode("START", start, "START"), NavNode("DEST", destination, "DEST")]
        nodes += self._road_nodes()
        nodes += self._bridge_nodes()
        nodes += self._barricade_nodes()
        nodes += self._building_nodes()
        if _with_area_nodes:
            nodes += self._area_nodes(unit)
        nodes = self._dedupe(nodes)
        start_i = next(i for i, n in enumerate(nodes) if n.key == "START")
        dest_i = next(i for i, n in enumerate(nodes) if n.key == "DEST")

        # Expand visibility edges only from nodes A* actually visits. Area
        # fallback portals can be numerous; eagerly building every pair makes
        # an otherwise local detour disproportionately expensive.
        edge_costs = {}
        def neighbors(i):
            for j in range(len(nodes)):
                if j == i or j in closed:
                    continue
                key = (i,j)
                if key not in edge_costs:
                    a,b = nodes[key[0]].pos,nodes[key[1]].pos
                    edge_costs[key] = (self._segment_time_cost(unit,a,b)
                                       if self.segment_passable(unit,a,b) else None)
                cost = edge_costs[key]
                if cost is not None and math.isfinite(cost):
                    yield j,cost

        inf = float("inf")
        g = [inf] * len(nodes); g[start_i] = 0.0
        parent: Dict[int, int] = {}
        heap = [(self._heuristic(unit, start, destination), 0.0, start_i)]
        closed = set()
        # Each node is expanded at most once, so effort is bounded by the node count; terrain
        # complexity itself is capped at load time (mnsim/validation.py).
        while heap:
            _, cur_g, i = heapq.heappop(heap)
            if i in closed:
                continue
            closed.add(i)
            if i == dest_i:
                break
            for j, edge_cost in neighbors(i):
                ng = cur_g + edge_cost
                if ng + 1e-9 < g[j]:
                    g[j] = ng; parent[j] = i
                    f = ng + self._heuristic(unit, nodes[j].pos, destination)
                    heapq.heappush(heap, (f, ng, j))

        if not math.isfinite(g[dest_i]):
            if not _with_area_nodes:
                return self.plan(unit,start,destination,_with_area_nodes=True)
            return [start]
        idx = dest_i; rev = [idx]
        while idx != start_i:
            idx = parent[idx]; rev.append(idx)
        rev.reverse()
        return [nodes[i].pos for i in rev]

    def _bridge_signature(self):
        return tuple((str(b.get("id")), self.terrain.bridge_operational(b)) for b in self.terrain.bridges)

    def next_waypoint(self, unit, destination: Vec2) -> Vec2:
        destination=(float(destination[0]),float(destination[1]))
        arrival = max(3.0, float(self.config.get("waypoint_arrival_m", 10.0)))
        # Local tactical maneuver should not thrash the strategic road graph as a tracked enemy
        # position moves every sensor update.  For short, physically clear legs, use direct motion;
        # river/bridge constraints still force A* because the direct segment is then impassable.
        if math.dist(unit.pos,destination) <= float(self.config.get("local_direct_route_m",1200.0)):
            if self.segment_passable(unit,tuple(unit.pos),destination):
                unit.metadata.pop("_nav_no_path", None)
                unit.metadata.pop("_nav_no_path_destination", None)
                return destination
        cached_dest=unit.metadata.get("_nav_destination")
        cached_sig=unit.metadata.get("_nav_bridge_signature")
        route=unit.metadata.get("_nav_route")
        idx=int(unit.metadata.get("_nav_route_index",1))
        sig=self._bridge_signature()
        dest_same=isinstance(cached_dest,(list,tuple)) and math.dist(tuple(cached_dest),destination)<=float(self.config.get("replan_destination_shift_m",75.0))
        valid=isinstance(route,list) and len(route)>=2 and dest_same and cached_sig==sig
        # Replan if the route target changed, infrastructure changed, or the formation was displaced
        # far away from its current route segment by local doctrine/combat behavior.
        if valid and 0 < idx < len(route):
            prev=tuple(route[max(0,idx-1)]); nxt=tuple(route[idx])
            # Distance to the current segment is a cheap route-deviation test.
            ax,ay=prev; bx,by=nxt; px,py=unit.pos; dx,dy=bx-ax,by-ay
            if dx*dx+dy*dy>1e-9:
                t=max(0.0,min(1.0,((px-ax)*dx+(py-ay)*dy)/(dx*dx+dy*dy)))
                q=(ax+t*dx,ay+t*dy); deviation=math.dist(unit.pos,q)
            else:
                deviation=math.dist(unit.pos,nxt)
            valid=deviation<=float(self.config.get("replan_deviation_m",80.0))
        if valid and 0 < idx < len(route):
            valid=self.segment_passable(unit,tuple(unit.pos),tuple(route[idx]))
        if not valid:
            planned=self.plan(unit, unit.pos, destination)
            route=[list(p) for p in planned]
            idx=1
            unit.metadata["_nav_destination"]=list(destination)
            unit.metadata["_nav_bridge_signature"]=sig
            unit.metadata["_nav_route"]=route
            unit.metadata["_nav_route_index"]=idx
        if len(route)<=1:
            # Explicitly expose an unreachable result to the order layer.  Returning the
            # current position alone is otherwise indistinguishable from "not there yet",
            # which caused MOVE/ATTACK missions to retry forever after bridge destruction.
            unit.metadata["_nav_no_path"]=True
            unit.metadata["_nav_no_path_destination"]=list(destination)
            return tuple(unit.pos)
        unit.metadata.pop("_nav_no_path", None)
        unit.metadata.pop("_nav_no_path_destination", None)
        while idx < len(route)-1 and math.dist(unit.pos,tuple(route[idx]))<=arrival:
            # Do not cut an obstacle corner merely because the formation entered the generic
            # waypoint-arrival radius. Advance only when the next leg is already continuously
            # passable from the *current* position. Otherwise keep closing on this portal/corner
            # until the wall has actually been cleared.
            next_point=tuple(route[idx+1])
            if not self.segment_passable(unit,tuple(unit.pos),next_point):
                break
            idx+=1
            unit.metadata["_nav_route_index"]=idx
        if idx < len(route):
            return tuple(route[idx])
        return destination
