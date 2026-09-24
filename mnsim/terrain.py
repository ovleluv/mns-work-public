
from __future__ import annotations
import math
from .traversal import BOUNDARY_EPS_M, movement_cuts, movement_regions
from typing import Any, Dict, List, Tuple

Vec2 = Tuple[float,float]

#: Length of one MIL1-class field barrier segment built by BUILD_BARRICADE (metres).
BARRICADE_LENGTH_M = 10.0
#: Branches whose dismounted (FOOT) formations may swim across lakes by default.  A unit type can
#: opt in explicitly with ``mobility_capabilities: ["SWIM"]`` regardless of branch.
FOOT_SWIMMER_BRANCHES = frozenset({"INFANTRY", "RECON", "SPECIAL_OPERATIONS"})

def _point_segment_distance(p:Vec2,a:Vec2,b:Vec2)->float:
    px,py=p; ax,ay=a; bx,by=b
    dx,dy=bx-ax,by-ay
    if dx==0 and dy==0: return math.dist(p,a)
    t=max(0.0,min(1.0,((px-ax)*dx+(py-ay)*dy)/(dx*dx+dy*dy)))
    q=(ax+t*dx,ay+t*dy)
    return math.dist(p,q)

def _point_in_poly(p:Vec2, poly:List[Vec2])->bool:
    x,y=p; inside=False
    j=len(poly)-1
    for i in range(len(poly)):
        xi,yi=poly[i]; xj,yj=poly[j]
        cross=((yi>y)!=(yj>y)) and (x < (xj-xi)*(y-yi)/(yj-yi+1e-12)+xi)
        if cross: inside=not inside
        j=i
    return inside

def _point_polygon_boundary_distance(p:Vec2, poly:List[Vec2])->float:
    if len(poly)<2:return 0.0
    return min(_point_segment_distance(p,poly[i],poly[(i+1)%len(poly)]) for i in range(len(poly)))

class TerrainModel:
    """Small vector-terrain layer.

    Vector terrain with mobility, vegetation/building LOS, water bodies, destructible
    infrastructure and authored elevation contours.  Terrain remains data-driven: new
    terrain polygons expose capabilities/modifiers instead of branching on unit names.
    """

    def __init__(self, data:Dict[str,Any]|None=None, navigation_config:Dict[str,Any]|None=None):
        self.data=data or {}
        self.navigation_config=dict(navigation_config or {})
        self.roads=self.data.get("roads",[])
        self.rivers=self.data.get("rivers",[])
        self.bridges=self.data.get("bridges",[])
        self.barricades=self.data.get("barricades",[])
        self.areas=self.data.get("areas",[])
        self._units_provider = None
        # Persistent area infrastructure (currently BUILDING) keeps damage state in the terrain
        # model so UI, LOS, combat and AAR see one truth.
        for area in self.areas:
            if str(area.get("type","")).upper()=="BUILDING":
                area.setdefault("max_integrity", float(area.get("structural_integrity", 180.0)))
                area.setdefault("integrity", float(area.get("max_integrity", area.get("structural_integrity",180.0))))
                area.setdefault("destroyed", False)
                # Building occupancy capacity is intentionally NOT enforced by the simulation
                # engine. Higher-level BML/COA generation may impose scenario-specific limits.
                area.setdefault("height_m", 8.0)
        # Bridges are persistent infrastructure objects.  State lives in the terrain model so
        # mobility, UI, AAR, and indirect-fire damage all observe the same truth.
        for br in self.bridges:
            br.setdefault("max_integrity", float(br.get("structural_integrity", 140.0)))
            br.setdefault("integrity", float(br.get("max_integrity", br.get("structural_integrity", 140.0))))
            br.setdefault("destroyed", False)
        from .pathfinding import NavigationPlanner
        self.navigation = NavigationPlanner(self, self.navigation_config)


    # ---------- field fortifications / HESCO-style barriers ----------
    @staticmethod
    def barricade_endpoints(barrier:Dict[str,Any])->Tuple[Vec2,Vec2]:
        c=tuple(map(float,barrier.get("center",(0.0,0.0))))
        length=float(barrier.get("length_m",10.0)); h=math.radians(float(barrier.get("heading_deg",0.0)))
        # heading is the outward/facing NORMAL.  The physical wall runs perpendicular to it.
        tx,ty=-math.sin(h),math.cos(h); half=length/2.0
        return ((c[0]-tx*half,c[1]-ty*half),(c[0]+tx*half,c[1]+ty*half))

    @staticmethod
    def _segments_intersect(a:Vec2,b:Vec2,c:Vec2,d:Vec2)->bool:
        def orient(p,q,r):
            return (q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0])
        o1,o2,o3,o4=orient(a,b,c),orient(a,b,d),orient(c,d,a),orient(c,d,b)
        eps=1e-8
        if ((o1>eps and o2<-eps) or (o1<-eps and o2>eps)) and ((o3>eps and o4<-eps) or (o3<-eps and o4>eps)):
            return True
        # Near-collinear contact counts as a crossing for movement/cover purposes.
        return min(_point_segment_distance(c,a,b),_point_segment_distance(d,a,b),_point_segment_distance(a,c,d),_point_segment_distance(b,c,d)) < 0.25

    def add_barricade(self, center:Vec2, heading_deg:float, builder_uid:str|None=None, barrier_id:str|None=None):
        used={str(x.get("id","")) for x in self.barricades}; n=1
        if barrier_id is None:
            while f"HESCO{n}" in used:n+=1
            barrier_id=f"HESCO{n}"
        b={"id":str(barrier_id),"type":"HESCO_MIL1","center":[round(float(center[0]),2),round(float(center[1]),2)],
           "heading_deg":float(heading_deg)%360.0,"length_m":BARRICADE_LENGTH_M,"width_m":1.06,"builder_uid":builder_uid,
           "small_arms_cover_factor":0.40,"heavy_direct_cover_factor":0.70}
        self.barricades.append(b); return b

    def segment_crosses_barricade(self,a:Vec2,b:Vec2)->bool:
        for wall in self.barricades:
            c,d=self.barricade_endpoints(wall)
            if self._segments_intersect(a,b,c,d):return True
        return False

    def barricade_cover(self, shooter:Vec2, target:Vec2):
        """Return directional cover if a barrier lies between shooter and target.

        ``heading_deg`` is the exposed/outward normal.  Protection belongs to the opposite side,
        so the target must lie behind the wall and the shooter in front of it.  This deliberately
        models frontal cover rather than an omnidirectional armor bonus.
        """
        best=None
        for wall in self.barricades:
            c=tuple(map(float,wall.get("center",(0,0)))); h=math.radians(float(wall.get("heading_deg",0.0)))
            nx,ny=math.cos(h),math.sin(h)
            ts=(target[0]-c[0])*nx+(target[1]-c[1])*ny
            ss=(shooter[0]-c[0])*nx+(shooter[1]-c[1])*ny
            if ts>=0.0 or ss<=0.0:continue
            e0,e1=self.barricade_endpoints(wall)
            if not self._segments_intersect(shooter,target,e0,e1):continue
            # Cover is useful to troops immediately behind the wall, not an entire half-map.
            if _point_segment_distance(target,e0,e1)>18.0:continue
            best=wall;break
        return best

    def on_road(self,p:Vec2)->bool:
        for road in self.roads:
            pts=[tuple(x) for x in road.get("points",[])]
            width=float(road.get("width_m",30.0))
            for a,b in zip(pts,pts[1:]):
                if _point_segment_distance(p,a,b)<=width/2+BOUNDARY_EPS_M: return True
        return False

    def river_at(self,p:Vec2):
        """Return the authored river containing *p*, if any.

        Backward-compatible polygons are supported, but a river may now be authored more naturally
        as a polyline ``points`` plus ``width_m``.  The latter is easier for scenario/terrain editors
        and produces believable bends without requiring a hand-built bank polygon.
        """
        for river in self.rivers:
            poly=[tuple(x) for x in river.get("polygon",[])]
            if poly and (_point_in_poly(p,poly) or _point_polygon_boundary_distance(p,poly)<=BOUNDARY_EPS_M): return river
            pts=[tuple(x) for x in river.get("points",[])]
            width=float(river.get("width_m",0.0))
            if len(pts)>=2 and width>0:
                if any(_point_segment_distance(p,a,b)<=width/2.0+BOUNDARY_EPS_M for a,b in zip(pts,pts[1:])):
                    return river
        return None

    def in_river(self,p:Vec2)->bool:
        """Return whether *p* lies in river water."""
        if self.river_at(p) is not None:
            return True
        return False

    def area_at(self,p:Vec2):
        """Return authored polygon terrain areas containing *p* (WOODS/URBAN/MARSH/etc.)."""
        out=[]
        for area in self.areas:
            poly=[tuple(x) for x in area.get("polygon",[])]
            if poly and _point_in_poly(p,poly): out.append(area)
        return out

    def areas_of_type(self,p:Vec2,*types:str):
        wanted={str(x).upper() for x in types}
        return [a for a in self.area_at(p) if str(a.get("type","")).upper() in wanted]

    def elevation_at(self,p:Vec2)->float:
        """Continuous approximation from authored polygon contour/plateau zones.

        Each containing ELEVATION polygon supplies a target elevation. Near its boundary the height
        ramps from the next-lower contour over ``transition_width_m`` (default 80 m), avoiding
        artificial vertical cliffs in vector maps. Nested contours therefore form usable slopes.
        """
        zones=[]
        for a in self.area_at(p):
            if str(a.get("type","")).upper()=="ELEVATION":
                zones.append((float(a.get("elevation_m",0.0)),a))
        if not zones:return 0.0
        current=0.0
        for target,a in sorted(zones,key=lambda x:x[0]):
            poly=[tuple(x) for x in a.get("polygon",[])]
            width=max(1.0,float(a.get("transition_width_m",80.0)))
            alpha=min(1.0,_point_polygon_boundary_distance(p,poly)/width) if poly else 1.0
            current=current+(target-current)*alpha
        return current

    def building_at(self,p:Vec2, include_destroyed:bool=False):
        for a in self.area_at(p):
            if str(a.get("type","")).upper()=="BUILDING":
                if include_destroyed or not bool(a.get("destroyed",False)):
                    return a
        return None

    def lake_at(self,p:Vec2):
        return next((a for a in self.area_at(p) if str(a.get("type","")).upper()=="LAKE"),None)

    def building_by_id(self,building_id:str):
        bid=str(building_id)
        return next((a for a in self.areas if str(a.get("type","")).upper()=="BUILDING" and str(a.get("id"))==bid),None)

    @staticmethod
    def building_center(b:Dict[str,Any])->Vec2:
        poly=[tuple(map(float,x)) for x in b.get("polygon",[])]
        if not poly:return (0.0,0.0)
        return (sum(x for x,y in poly)/len(poly),sum(y for x,y in poly)/len(poly))

    @staticmethod
    def building_operational(b:Dict[str,Any])->bool:
        return not bool(b.get("destroyed",False)) and float(b.get("integrity",1.0))>0.0

    def building_has_capacity(self,unit,building:Dict[str,Any])->bool:
        """Compatibility shim: engine-level building occupancy is deliberately unlimited.

        Capacity/echelon/personnel constraints belong to the BML/COA generation layer, not the
        movement/terrain truth model. Existing callers therefore always receive True.
        """
        return True

    @staticmethod
    def _building_access_allowed(unit, building:Dict[str,Any])->bool:
        bid=str(building.get("id",""))
        return bool(bid) and str(getattr(unit,"metadata",{}).get("_building_access_id",""))==bid

    def apply_building_damage(self, building:Dict[str,Any], damage:float, source:str="", weapon:str=""):
        if building is None or not self.building_operational(building):return None
        old=float(building.get("integrity",building.get("max_integrity",180.0)))
        new=max(0.0,old-max(0.0,float(damage))); building["integrity"]=new
        if new<=0.0:building["destroyed"]=True
        return {"building":str(building.get("id","BUILDING")),"damage":old-new,"integrity":new,
                "max_integrity":float(building.get("max_integrity",180.0)),"destroyed":bool(building.get("destroyed",False)),
                "source":source,"weapon":weapon}

    def apply_building_impact(self, impact:Vec2, weapon_metadata:Dict[str,Any], rng):
        out=[]
        for b in self.areas:
            if str(b.get("type","")).upper()!="BUILDING" or not self.building_operational(b):continue
            poly=[tuple(x) for x in b.get("polygon",[])]
            if not poly:continue
            # Point-in-footprint is a direct structural hit; close impacts retain a small blast effect.
            inside=_point_in_poly(impact,poly)
            if inside:
                lo=float(weapon_metadata.get("building_direct_damage_min",30.0)); hi=float(weapon_metadata.get("building_direct_damage_max",55.0))
            else:
                d=min((_point_segment_distance(impact,poly[i],poly[(i+1)%len(poly)]) for i in range(len(poly))),default=1e9)
                near=float(weapon_metadata.get("building_near_effect_m",18.0))
                if d>near:continue
                lo=float(weapon_metadata.get("building_near_damage_min",4.0)); hi=float(weapon_metadata.get("building_near_damage_max",12.0))
            rec=self.apply_building_damage(b,rng.uniform(lo,max(lo,hi)),weapon=weapon_metadata.get("ui_range_label","INDIRECT"))
            if rec:out.append(rec)
        return out

    def slope_angle_deg(self,a:Vec2,b:Vec2)->float:
        d=max(1e-6,math.dist(a,b)); dz=self.elevation_at(b)-self.elevation_at(a)
        return math.degrees(math.atan2(dz,d))

    def bridge_by_id(self, bridge_id:str):
        bid=str(bridge_id)
        return next((b for b in self.bridges if str(b.get("id"))==bid), None)

    @staticmethod
    def bridge_operational(br:Dict[str,Any])->bool:
        return not bool(br.get("destroyed",False)) and float(br.get("integrity",1.0))>0.0

    @staticmethod
    def _bridge_local(p:Vec2, br:Dict[str,Any])->Vec2:
        cx,cy=br["center"]; ang=math.radians(float(br.get("heading_deg",90.0)))
        # local +Y is bridge long/crossing axis; local +X is deck width.
        dx,dy=p[0]-cx,p[1]-cy
        ux,uy=math.cos(ang),math.sin(ang)
        # projection on long axis, and perpendicular width axis
        along=dx*ux+dy*uy; across=-dx*uy+dy*ux
        return across,along


    @staticmethod
    def bridge_points(br:Dict[str,Any])->List[Vec2]:
        """Return authored bridge centerline points, preserving legacy rectangle bridges."""
        pts=[(float(p[0]),float(p[1])) for p in br.get("points",[]) if len(p)>=2]
        if len(pts)>=2:
            return pts
        cx,cy=map(float,br["center"]); length=float(br.get("length_m",180.0))
        ang=math.radians(float(br.get("heading_deg",90.0)))
        ux,uy=math.cos(ang),math.sin(ang); half=length/2.0
        return [(cx-ux*half,cy-uy*half),(cx+ux*half,cy+uy*half)]

    @classmethod
    def bridge_center(cls,br:Dict[str,Any])->Vec2:
        pts=cls.bridge_points(br)
        if br.get("points") and len(pts)>=2:
            total=sum(math.dist(a,b) for a,b in zip(pts,pts[1:]))
            if total<=1e-9:return pts[0]
            target=total/2.0; walked=0.0
            for a,b in zip(pts,pts[1:]):
                seg=math.dist(a,b)
                if walked+seg>=target:
                    t=(target-walked)/max(seg,1e-9)
                    return (a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t)
                walked+=seg
        return tuple(map(float,br.get("center",pts[0])))

    @classmethod
    def _distance_to_bridge_deck(cls,p:Vec2,br:Dict[str,Any])->float:
        pts=cls.bridge_points(br); half=float(br.get("width_m",80.0))/2.0
        if len(pts)<2:return math.dist(p,pts[0])
        d=min(_point_segment_distance(p,a,b) for a,b in zip(pts,pts[1:]))
        return max(0.0,d-half)

    def on_bridge(self,p:Vec2)->bool:
        for br in self.bridges:
            if not self.bridge_operational(br): continue
            if self._distance_to_bridge_deck(p,br)<=BOUNDARY_EPS_M:return True
        return False

    def nearest_bridge(self,p:Vec2,dest:Vec2)->Vec2|None:
        available=[b for b in self.bridges if self.bridge_operational(b)]
        if not available: return None
        centers=[self.bridge_center(b) for b in available]
        return min(centers,key=lambda q: math.dist(p,q)+math.dist(q,dest))

    @classmethod
    def _distance_to_bridge_rect(cls,p:Vec2, br:Dict[str,Any])->float:
        # Kept under the historical method name for damage-model compatibility.
        return cls._distance_to_bridge_deck(p,br)

    def apply_bridge_impact(self, impact:Vec2, weapon_metadata:Dict[str,Any], rng):
        """Apply cumulative artillery structural damage and return damage records.

        This is intentionally an aggregate structural-integrity model rather than a detailed
        finite-element bridge model.  Deck hits cause substantial damage; very near impacts may
        cause smaller abutment/structural damage.  Values are data-driven and replaceable later
        by bridge/munition vulnerability tables.
        """
        out=[]
        for br in self.bridges:
            if not self.bridge_operational(br): continue
            d=self._distance_to_bridge_rect(impact,br)
            near=float(weapon_metadata.get("bridge_near_effect_m",18.0))
            if d<=1e-6:
                lo=float(weapon_metadata.get("bridge_deck_damage_min",22.0)); hi=float(weapon_metadata.get("bridge_deck_damage_max",38.0)); geom="DECK"
            elif d<=near:
                lo=float(weapon_metadata.get("bridge_near_damage_min",3.0)); hi=float(weapon_metadata.get("bridge_near_damage_max",9.0)); geom="NEAR"
            else:
                continue
            damage=max(0.0,rng.uniform(lo,max(lo,hi)))
            old=float(br.get("integrity",br.get("max_integrity",140.0)))
            new=max(0.0,old-damage); br["integrity"]=new
            destroyed=new<=0.0
            if destroyed: br["destroyed"]=True
            out.append({"bridge":str(br.get("id")),"damage":damage,"integrity":new,"max_integrity":float(br.get("max_integrity",140.0)),"geometry":geom,"destroyed":destroyed})
        return out


    @staticmethod
    def _poly_bbox(poly:List[Vec2]):
        if not poly:return (0.0,0.0,0.0,0.0)
        xs=[p[0] for p in poly]; ys=[p[1] for p in poly]
        return (min(xs),min(ys),max(xs),max(ys))

    @staticmethod
    def _segment_bbox_overlap(a:Vec2,b:Vec2,bbox)->bool:
        x0,y0,x1,y1=bbox
        return not (max(a[0],b[0])<x0 or min(a[0],b[0])>x1 or max(a[1],b[1])<y0 or min(a[1],b[1])>y1)

    @staticmethod
    def _segment_intersection_t(a:Vec2,b:Vec2,c:Vec2,d:Vec2):
        rx,ry=b[0]-a[0],b[1]-a[1]; sx,sy=d[0]-c[0],d[1]-c[1]
        den=rx*sy-ry*sx
        if abs(den)<1e-10:return None
        qx,qy=c[0]-a[0],c[1]-a[1]
        t=(qx*sy-qy*sx)/den; u=(qx*ry-qy*rx)/den
        if -1e-9<=t<=1.0+1e-9 and -1e-9<=u<=1.0+1e-9:return max(0.0,min(1.0,t))
        return None

    @classmethod
    def _line_intervals_inside_polygon(cls,a:Vec2,b:Vec2,poly:List[Vec2]):
        """Return t-intervals of segment a-b lying inside polygon without radial sampling.

        Cost is O(number of polygon edges), independent of sight-line length. This is both faster
        and less sensitive to map scale than the previous 4--8 m marching implementation.
        """
        if len(poly)<3 or not cls._segment_bbox_overlap(a,b,cls._poly_bbox(poly)):return []
        ts=[0.0,1.0]
        for i in range(len(poly)):
            t=cls._segment_intersection_t(a,b,poly[i],poly[(i+1)%len(poly)])
            if t is not None:ts.append(t)
        ts=sorted(set(round(x,10) for x in ts))
        out=[]
        for lo,hi in zip(ts,ts[1:]):
            if hi-lo<=1e-9:continue
            m=(lo+hi)*0.5; q=(a[0]+(b[0]-a[0])*m,a[1]+(b[1]-a[1])*m)
            if _point_in_poly(q,poly):out.append((lo,hi))
        return out

    @classmethod
    def _line_length_inside_polygon(cls,a:Vec2,b:Vec2,poly:List[Vec2],spacing_m:float=8.0)->float:
        d=math.dist(a,b)
        return d*sum(hi-lo for lo,hi in cls._line_intervals_inside_polygon(a,b,poly))

    def _ray_clears_area_height(self,a:Vec2,b:Vec2,area:Dict[str,Any],obstacle_height_m:float,eye_a:float=1.7,eye_b:float=1.7)->bool:
        """Approximate 3-D clearance using polygon intersection intervals, not ray marching.

        Three representative samples per crossed interval are sufficient for the authored smooth
        contour model and make cost depend on polygon complexity rather than sight-line length.
        """
        poly=[tuple(x) for x in area.get("polygon",[])]
        if not poly or _point_in_poly(a,poly) or _point_in_poly(b,poly):return False
        intervals=self._line_intervals_inside_polygon(a,b,poly)
        if not intervals:return False
        za=self.elevation_at(a)+eye_a; zb=self.elevation_at(b)+eye_b
        for lo,hi in intervals:
            for t in (lo+(hi-lo)*0.2,(lo+hi)*0.5,lo+(hi-lo)*0.8):
                q=(a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t)
                line_z=za+(zb-za)*t; top=self.elevation_at(q)+float(obstacle_height_m)
                if line_z<=top+0.5:return False
        return True

    def _building_roof_m(self, building:Dict[str,Any], poly:List[Vec2]|None=None)->float:
        """Absolute roof elevation; shared by observation, direct fire and the UI envelope so a
        building can never block sight but not fire (or the reverse)."""
        poly=poly if poly is not None else [tuple(x) for x in building.get("polygon",[])]
        ground=building.get("ground_elevation_m")
        if ground is None:
            ground=self.elevation_at(tuple(poly[0])) if poly else 0.0
        return float(ground)+float(building.get("height_m",8.0))

    # ---------- terrain (ridge/crest) line of sight ----------
    def _elevation_signature(self):
        return tuple((str(a.get("id","")),float(a.get("elevation_m",0.0)),len(a.get("polygon",[])))
                     for a in self.areas if str(a.get("type","")).upper()=="ELEVATION")

    def _dem(self):
        """Lazily rasterised height field of the authored contour model (for LOS only).

        ``elevation_at`` is exact but costs one pass over every polygon; a sight line needs tens
        of height samples, so LOS reads a bilinear DEM built once per contour set.
        """
        sig=self._elevation_signature()
        cached=getattr(self,"_dem_cache",None)
        if cached is not None and cached[0]==sig:
            return cached[1]
        if not sig:
            self._dem_cache=(sig,None); return None
        world=getattr(self,"world",None) or {}
        xs=[float(p[0]) for a in self.areas if str(a.get("type","")).upper()=="ELEVATION" for p in a.get("polygon",[])]
        ys=[float(p[1]) for a in self.areas if str(a.get("type","")).upper()=="ELEVATION" for p in a.get("polygon",[])]
        x0=min([0.0]+xs); y0=min([0.0]+ys)
        x1=max([float(world.get("width_m",0.0))]+xs); y1=max([float(world.get("height_m",0.0))]+ys)
        cell=float(self.navigation_config.get("los_dem_cell_m",20.0))
        max_cells=int(self.navigation_config.get("los_dem_max_cells",250_000))
        cell=max(cell,math.sqrt(max(1.0,(x1-x0)*(y1-y0))/max(1,max_cells)))
        nx=int(math.ceil((x1-x0)/cell))+1; ny=int(math.ceil((y1-y0)/cell))+1
        grid=[[self.elevation_at((x0+i*cell,y0+j*cell)) for i in range(nx)] for j in range(ny)]
        dem=(x0,y0,cell,nx,ny,grid)
        self._dem_cache=(sig,dem); self._los_cache={}
        return dem

    def dem_height(self, p:Vec2)->float:
        dem=self._dem()
        if dem is None:
            return 0.0
        x0,y0,cell,nx,ny,grid=dem
        fx=min(max((p[0]-x0)/cell,0.0),nx-1.000001); fy=min(max((p[1]-y0)/cell,0.0),ny-1.000001)
        i=int(fx); j=int(fy); tx=fx-i; ty=fy-j
        r0=grid[j]; r1=grid[j+1]
        return ((r0[i]*(1-tx)+r0[i+1]*tx)*(1-ty)+(r1[i]*(1-tx)+r1[i+1]*tx)*ty)

    def terrain_masks(self, a:Vec2, b:Vec2, height_a:float=1.7, height_b:float=1.7)->bool:
        """True when the ground rises above the straight sight line between two eye points."""
        dem=self._dem()
        if dem is None:
            return False
        cell=dem[2]
        key=(round(a[0]/5.0),round(a[1]/5.0),round(b[0]/5.0),round(b[1]/5.0),round(height_a,1),round(height_b,1))
        cache=getattr(self,"_los_cache",None)
        if cache is None:
            cache=self._los_cache={}
        if key in cache:
            return cache[key]
        d=math.dist(a,b)
        n=max(2,min(400,int(math.ceil(d/(0.75*cell)))))
        za=self.dem_height(a)+height_a; zb=self.dem_height(b)+height_b
        clearance=float(self.navigation_config.get("los_clearance_m",0.5))
        masked=False
        for k in range(1,n):
            t=k/n
            q=(a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t)
            if self.dem_height(q)>za+(zb-za)*t-clearance:
                masked=True; break
        if len(cache)>200_000:
            cache.clear()
        cache[key]=masked
        return masked

    def observation_modifier(self, observer_pos:Vec2, target_pos:Vec2, sensor_mode:str="VISUAL") -> Dict[str,float]:
        """Return observation modifiers for the observer-target ray.

        Vegetation is treated as *path attenuation/penetration*, not as an observer-only range
        penalty.  The crossed path is reciprocal, while concealment is applied at the target end
        and may therefore make detection asymmetric.  This contract is also suitable for future
        smoke/building/elevation LOS layers.
        """
        out={"range_factor":1.0,"fov_factor":1.0,"awareness_factor":1.0,"detection_factor":1.0}
        if observer_pos!=target_pos and self.terrain_masks(observer_pos,target_pos,1.7,1.7):
            # A crest between observer and target: dead ground (reverse slope, defilade).
            out["range_factor"]=0.0; out["detection_factor"]=0.0
            return out
        zones=list(self.data.get("observation_zones",[]))
        zones += [a for a in self.areas if a.get("observation_modifier") or a.get("sensor_overrides")
                  or str(a.get("type","")).upper() in ("WOODS","FOREST","BRUSH","URBAN","BUILDING")]
        if not zones:
            return out
        mode=str(sensor_mode).upper()
        # Conservative engine fallbacks. Authored per-zone values always take precedence.
        penetration_defaults={
            "FOREST":{"VISUAL":75.0,"THERMAL":105.0},
            "WOODS":{"VISUAL":180.0,"THERMAL":240.0},
        }
        concealment_defaults={"FOREST":0.34,"WOODS":0.52,"BRUSH":0.78,"URBAN":0.70}
        for zone in zones:
            poly=[tuple(x) for x in zone.get("polygon",[])]
            if not poly:
                continue
            inside_len=self._line_length_inside_polygon(observer_pos,target_pos,poly)
            if inside_len<=0.0:
                continue
            ztype=str(zone.get("type","")).upper()
            canopy_defaults={"FOREST":18.0,"WOODS":12.0,"BRUSH":2.5}
            clears_height=(ztype in canopy_defaults and self._ray_clears_area_height(
                observer_pos,target_pos,zone,float(zone.get("obstacle_height_m",canopy_defaults[ztype]))))
            if clears_height:
                # Elevated line of sight passes above the intervening canopy. Keep endpoint target
                # concealment semantics below only when the target itself is in the vegetation.
                continue
            raw=dict(zone.get("observation_modifier",{}))
            per_sensor=dict(zone.get("sensor_overrides",{})).get(mode)
            if per_sensor:
                raw.update(dict(per_sensor))

            penetration=float(zone.get("max_visual_penetration_m",0.0))
            penetration_by_sensor=dict(zone.get("sensor_penetration_m",{}))
            if mode in penetration_by_sensor:
                penetration=float(penetration_by_sensor[mode])
            elif penetration<=0.0:
                penetration=float(penetration_defaults.get(ztype,{}).get(mode,0.0))

            # A sufficiently deep vegetation crossing blocks the sensor path in both directions.
            if penetration>0.0 and inside_len>penetration:
                out["detection_factor"]=0.0
                out["range_factor"]=0.0
                continue

            # Path effects scale with how much clutter the ray actually traverses.  Thus a unit at
            # a forest edge can observe far into open ground, instead of inheriting a blanket
            # "forest view range" penalty merely because its own point lies inside the polygon.
            exposure=min(1.0, inside_len/max(penetration,1.0)) if penetration>0.0 else 1.0
            for k in ("range_factor","fov_factor","awareness_factor","detection_factor"):
                base=max(0.0,float(raw.get(k,1.0)))
                out[k]*=base**exposure

            # LOS is reciprocal, detection is not: a target embedded in vegetation is harder to
            # pick out than an exposed target looking back along the same geometric ray.
            if _point_in_poly(target_pos,poly):
                target_factor=float(zone.get("target_concealment_factor",
                                    concealment_defaults.get(ztype,1.0)))
                sensor_conceal=dict(zone.get("target_concealment_by_sensor",{}))
                if mode in sensor_conceal:
                    target_factor=float(sensor_conceal[mode])
                out["detection_factor"]*=max(0.0,target_factor)

        # Buildings are hard geometric occluders unless the observer/target is actually inside the
        # same footprint. Elevation can see over a building when the straight sight line at the
        # obstacle exceeds its authored roof height. Occupants looking outward are not given a
        # blanket short range; concealment is applied to observers looking into the building.
        obs_z=self.elevation_at(observer_pos)+1.7
        tgt_z=self.elevation_at(target_pos)+1.7
        ray_len=max(1e-6,math.dist(observer_pos,target_pos))
        for bld in [a for a in self.areas if str(a.get("type","")).upper()=="BUILDING" and self.building_operational(a)]:
            poly=[tuple(x) for x in bld.get("polygon",[])]
            if not poly:continue
            inside_obs=_point_in_poly(observer_pos,poly); inside_tgt=_point_in_poly(target_pos,poly)
            crossed=self._line_length_inside_polygon(observer_pos,target_pos,poly,spacing_m=4.0)
            if crossed<=0:continue
            if inside_obs and inside_tgt:
                continue
            # Test representative points of the exact footprint intersection interval instead
            # of marching every ~6 m along the whole sightline.
            blocked=False
            roof=self._building_roof_m(bld,poly)
            intervals=self._line_intervals_inside_polygon(observer_pos,target_pos,poly)
            for lo,hi in intervals:
                for t in (lo+(hi-lo)*0.25,(lo+hi)*0.5,lo+(hi-lo)*0.75):
                    line_z=obs_z+(tgt_z-obs_z)*t
                    if line_z <= roof + 0.5:blocked=True;break
                if blocked:break
            if blocked and not (inside_obs or inside_tgt):
                out["range_factor"]=0.0;out["detection_factor"]=0.0
            elif inside_tgt and not inside_obs:
                out["detection_factor"]*=max(0.0,float(bld.get("external_detection_factor",0.42)))
                out["range_factor"]*=max(0.0,float(bld.get("external_range_factor",0.82)))
        return out


    def approx_visual_limit(self, observer_pos:Vec2, heading_deg:float, max_range_m:float, sensor_mode:str="VISUAL", awareness:bool=False)->float:
        """Cheap UI-only LOS envelope query.

        A single ray is tested once against candidate vegetation/building polygons using AABB
        rejection and exact 2-D intersection intervals. It intentionally approximates the full
        observation model: the simulation truth continues to use ``observation_modifier``.
        """
        rmax=max(0.0,float(max_range_m)); a=math.radians(float(heading_deg))
        end=(observer_pos[0]+math.cos(a)*rmax,observer_pos[1]+math.sin(a)*rmax)
        mode=str(sensor_mode).upper(); limit=rmax
        dem=self._dem()
        if dem is not None and rmax>0:
            eye=self.dem_height(observer_pos)+1.7; best=-1e9; step=max(5.0,0.75*dem[2])
            r=step
            while r<rmax:
                q=(observer_pos[0]+math.cos(a)*r,observer_pos[1]+math.sin(a)*r)
                g=self.dem_height(q)
                if (g+1.7-eye)/r < best:
                    limit=r; break        # a 1.7 m target here would be below an earlier crest
                best=max(best,(g-eye)/r); r+=step
        penetration_defaults={"FOREST":{"VISUAL":75.0,"THERMAL":105.0},"WOODS":{"VISUAL":180.0,"THERMAL":240.0}}
        for zone in self.areas:
            ztype=str(zone.get("type","")).upper()
            if ztype not in ("FOREST","WOODS","BRUSH","BUILDING"):continue
            if ztype=="BUILDING" and not self.building_operational(zone):continue
            poly=[tuple(x) for x in zone.get("polygon",[])]
            if len(poly)<3 or not self._segment_bbox_overlap(observer_pos,end,self._poly_bbox(poly)):continue
            intervals=self._line_intervals_inside_polygon(observer_pos,end,poly)
            if not intervals:continue
            if ztype=="BUILDING":
                in_o=_point_in_poly(observer_pos,poly)
                if in_o:continue
                lo,hi=intervals[0]; tm=(lo+hi)*0.5
                q=(observer_pos[0]+(end[0]-observer_pos[0])*tm,observer_pos[1]+(end[1]-observer_pos[1])*tm)
                line_z=self.elevation_at(observer_pos)+1.7
                roof=self._building_roof_m(zone,poly)
                # UI approximation assumes roughly level endpoint eye height; elevated observers
                # still benefit because terrain elevation is included at the observer and roof.
                if line_z<=roof+0.5:limit=min(limit,max(0.0,lo*rmax))
                continue
            penetration=float(dict(zone.get("sensor_penetration_m",{})).get(mode,zone.get("max_visual_penetration_m",0.0)))
            if penetration<=0.0:penetration=float(penetration_defaults.get(ztype,{}).get(mode,0.0))
            if penetration<=0.0:continue
            cumulative=0.0
            for lo,hi in intervals:
                seg=(hi-lo)*rmax
                if cumulative+seg>penetration:
                    limit=min(limit,lo*rmax+max(0.0,penetration-cumulative)); break
                cumulative+=seg
        return max(0.0,min(rmax,limit))

    def direct_fire_modifier(self, shooter_pos:Vec2, target_pos:Vec2) -> Dict[str,float]:
        """Return vegetation effects for a direct-fire ray.

        Unlike weapon nominal range, this models the amount of vegetation physically crossed by
        the firing line. A shooter near a forest edge may therefore fire far into open terrain,
        while a target deep inside vegetation may be impossible to engage even at short map range.
        Values are generic M&S tuning defaults, not weapon-specific real-world specifications.
        """
        out={"allowed":True,"effect_factor":1.0,"vegetation_path_m":0.0}
        if shooter_pos!=target_pos and self.terrain_masks(shooter_pos,target_pos,1.5,1.5):
            out["allowed"]=False; out["effect_factor"]=0.0; out["terrain_masked"]=True
            return out
        zones=[a for a in self.areas if str(a.get("type","")).upper() in ("FOREST","WOODS","BRUSH")]
        penetration_defaults={"FOREST":45.0,"WOODS":120.0,"BRUSH":220.0}
        effect_defaults={"FOREST":0.45,"WOODS":0.65,"BRUSH":0.82}
        for zone in zones:
            poly=[tuple(x) for x in zone.get("polygon",[])]
            if not poly:
                continue
            inside_len=self._line_length_inside_polygon(shooter_pos,target_pos,poly)
            if inside_len<=0.0:
                continue
            ztype=str(zone.get("type","")).upper()
            canopy_defaults={"FOREST":18.0,"WOODS":12.0,"BRUSH":2.5}
            if ztype in canopy_defaults and self._ray_clears_area_height(shooter_pos,target_pos,zone,float(zone.get("obstacle_height_m",canopy_defaults[ztype])),1.5,1.5):
                continue
            penetration=float(zone.get("max_direct_fire_penetration_m",
                              penetration_defaults.get(ztype,0.0)))
            out["vegetation_path_m"] += inside_len
            if penetration>0.0 and inside_len>penetration:
                out["allowed"]=False
                out["effect_factor"]=0.0
                return out
            exposure=min(1.0,inside_len/max(1.0,penetration)) if penetration>0.0 else 1.0
            full_penalty=max(0.0,min(1.0,float(zone.get("direct_fire_effect_factor",
                                                effect_defaults.get(ztype,1.0)))))
            out["effect_factor"] *= full_penalty**exposure
        # Buildings use the same elevation-aware hard-occlusion geometry as observation. A target
        # inside a building remains engageable through openings/windows abstractly, but enjoys a
        # strong hit penalty; a third-party building between shooter and target blocks the shot.
        obs_z=self.elevation_at(shooter_pos)+1.5; tgt_z=self.elevation_at(target_pos)+1.5
        ray_len=max(1e-6,math.dist(shooter_pos,target_pos))
        for bld in [a for a in self.areas if str(a.get("type","")).upper()=="BUILDING" and self.building_operational(a)]:
            poly=[tuple(x) for x in bld.get("polygon",[])]
            if not poly:continue
            crossed=self._line_length_inside_polygon(shooter_pos,target_pos,poly,spacing_m=4.0)
            if crossed<=0:continue
            in_s=_point_in_poly(shooter_pos,poly); in_t=_point_in_poly(target_pos,poly)
            if in_s and in_t:continue
            blocked=False; roof=self._building_roof_m(bld,poly)
            intervals=self._line_intervals_inside_polygon(shooter_pos,target_pos,poly)
            for lo,hi in intervals:
                for t in (lo+(hi-lo)*0.25,(lo+hi)*0.5,lo+(hi-lo)*0.75):
                    if (obs_z+(tgt_z-obs_z)*t)<=roof+0.5:blocked=True;break
                if blocked:break
            if blocked and not (in_s or in_t):
                out["allowed"]=False;out["effect_factor"]=0.0;out["building_blocked"]=str(bld.get("id"));return out
            if in_t and not in_s:
                out["effect_factor"]*=max(0.0,float(bld.get("external_direct_fire_factor",0.38)))
                out["target_building_id"]=str(bld.get("id"))
        wall=self.barricade_cover(shooter_pos,target_pos) if getattr(self,"barricades",None) else None
        if wall is not None:
            out["barricade_cover"]=str(wall.get("id","HESCO"))
            out["barricade_small_arms_factor"]=float(wall.get("small_arms_cover_factor",0.40))
            out["barricade_heavy_factor"]=float(wall.get("heavy_direct_cover_factor",0.70))
        return out

    def speed_factor(self,unit,p:Vec2,dest:Vec2|None=None)->float:
        base=self.terrain_speed_factor(unit.unit_type.metadata,p)
        base*=self.mobile_equipment_fraction(unit)
        if dest is not None and math.dist(p,dest)>1e-6:
            base*=self.grade_speed_factor(p,dest)
        return base

    def terrain_speed_factor(self,md:Dict[str,Any],p:Vec2)->float:
        """Surface/area/river factor at *p* for a unit-type metadata block (no unit state).

        Piecewise constant between road/bridge/river/area boundaries, which lets the planner
        test passability exactly per boundary interval instead of sampling every few metres.
        """
        mobility=str(md.get("mobility_class","FOOT")).upper()
        factors=md.get("terrain_speed_factors",{})
        on_bridge=self.on_bridge(p)
        on_road=self.on_road(p)
        if on_bridge:
            base=float(factors.get("BRIDGE",factors.get("ROAD",1.0)))
        elif on_road:
            base=float(factors.get("ROAD",1.0))
        else:
            base=float(factors.get("OPEN",1.0))

        # Optional polygon terrain areas add a data-driven local mobility modifier.  This does not
        # hard-code branch names: an area may specify a general factor plus mobility-class overrides.
        for area in self.area_at(p):
            if str(area.get("type","")).upper()=="ELEVATION":
                continue
            overrides=dict(area.get("mobility_overrides",{}))
            # A road clears vehicle restrictions in FOREST (see passable()).
            # Remove only that forest penalty; foot, other areas, water and grade
            # retain their existing modifiers.
            if (str(area.get("type","")).upper()=="FOREST"
                    and mobility in {"TRACKED","WHEELED","WHEELED_TOWED"}
                    and on_road):
                forbidden={str(x).upper() for x in area.get("impassable_mobility_classes",[])}
                if mobility in forbidden or (mobility in overrides and float(overrides[mobility])<=0.0):
                    continue
            am=float(area.get("movement_factor",1.0))
            am=float(overrides.get(mobility,am))
            base*=max(0.0,am)

        river=self.river_at(p)
        if river is not None and not on_bridge:
            river_factor=river.get("movement_factor",None)
            ov=dict(river.get("mobility_overrides",{}))
            if mobility in ov:
                river_factor=ov[mobility]
            if river_factor is not None:
                base*=max(0.0,float(river_factor))
        return base

    @staticmethod
    def mobile_equipment_fraction(unit)->float:
        equipment=[e for e in unit.elements.values() if e.category.upper()=="EQUIPMENT" and ("ARMOR" in e.tags or "ARTILLERY" in e.tags)]
        if not equipment:
            return 1.0
        total=sum(max(1,e.count) for e in equipment)
        mobile=sum(e.mobile_item_count for e in equipment)
        # Formation may continue with surviving mobile vehicles; a fully mobility-killed
        # detached proxy has max_speed=0 and therefore remains stationary.
        return max(0.0,min(1.0,mobile/max(1,total)))

    def grade_speed_factor(self,p:Vec2,dest:Vec2)->float:
        # Formation-scale grade penalty. Mild downhill is useful, steep downhill also slows.
        ang=self.slope_angle_deg(p,dest)
        if ang>0: return max(0.22,1.0-ang/38.0)
        return max(0.35,1.0-abs(ang)/55.0)

    def has_elevation(self)->bool:
        return any(str(a.get("type","")).upper()=="ELEVATION" for a in self.areas)

    def revision(self)->tuple:
        """Cheap signature of mutable terrain state used to invalidate planner caches."""
        return (len(self.areas),len(self.roads),len(self.rivers),len(self.barricades),
                tuple(self.bridge_operational(b) for b in self.bridges),
                tuple(self.building_operational(a) for a in self.areas
                      if str(a.get("type","")).upper()=="BUILDING"))

    def speed_boundary_cuts(self,a:Vec2,b:Vec2)->List[float]:
        """Segment parameters where any speed-relevant membership may change."""
        from .traversal import polygon_cuts, capsule_cuts
        cuts={0.0,1.0}
        for area in self.areas:
            if str(area.get("type","")).upper()=="ELEVATION":
                continue
            poly=area.get("polygon",[])
            if len(poly)>=3 and self._segment_bbox_overlap(a,b,self._poly_bbox(poly)):
                cuts.update(polygon_cuts(a,b,[tuple(x) for x in poly]))
        for river in self.rivers:
            poly=river.get("polygon",[])
            if len(poly)>=3 and self._segment_bbox_overlap(a,b,self._poly_bbox(poly)):
                cuts.update(polygon_cuts(a,b,[tuple(x) for x in poly]))
        lines=[(r.get("points",[]),float(r.get("width_m",30.0))/2.0) for r in self.roads]
        lines+=[(r.get("points",[]),float(r.get("width_m",0.0))/2.0) for r in self.rivers]
        lines+=[(self.bridge_points(r),float(r.get("width_m",80.0))/2.0) for r in self.bridges]
        for points,radius in lines:
            if radius<=0:continue
            for c,d in zip(points,points[1:]):
                bbox=(min(c[0],d[0])-radius,min(c[1],d[1])-radius,max(c[0],d[0])+radius,max(c[1],d[1])+radius)
                if self._segment_bbox_overlap(a,b,bbox):
                    cuts.update(capsule_cuts(a,b,tuple(c),tuple(d),radius))
        return sorted(t for t in cuts if 0.0<=t<=1.0)

    def passable(self,unit,p:Vec2)->bool:
        md=unit.unit_type.metadata
        mobility=str(md.get("mobility_class","FOOT")).upper()
        # Lakes are water bodies: foot infantry may swim, while vehicles/equipment require an
        # explicit amphibious/water-crossing capability. Heavy infantry weapons are handled by the
        # simulation transition hook when a foot formation actually enters the water.
        # Movement treats polygon boundaries as closed. Keep LOS/area queries'
        # historical point-in-polygon convention independent of this policy.
        areas=[]
        for area in self.areas:
            poly=area.get("polygon",[])
            if not poly or not self._segment_bbox_overlap(p,p,self._poly_bbox(poly)):continue
            if _point_in_poly(p,poly) or _point_polygon_boundary_distance(p,poly)<=BOUNDARY_EPS_M:
                areas.append(area)
        lake=next((a for a in areas if str(a.get("type","")).upper()=="LAKE"),None)
        water_override=False
        if lake is not None:
            caps={str(x).upper() for x in md.get("mobility_capabilities",[])}
            branch=str(getattr(unit.unit_type,"branch","")).upper()
            foot_swimmer=(mobility=="FOOT" and (branch in FOOT_SWIMMER_BRANCHES or "SWIM" in caps))
            water_override=bool(caps & {"AMPHIBIOUS","WATER_CROSSING"})
            if not foot_swimmer and not water_override:return False
        bld=next((a for a in areas if str(a.get("type","")).upper()=="BUILDING" and self.building_operational(a)),None)
        if bld is not None:
            # Operational buildings are hard movement obstacles by default. Only an explicit
            # ENTER_BUILDING / EXIT_BUILDING order grants temporary access through this footprint.
            if mobility!="FOOT":return False
            if not self._building_access_allowed(unit,bld):return False
        # Polygon areas may explicitly forbid mobility classes.  Roads are deliberate cleared
        # corridors, so a vehicle can traverse a dense FOREST only while it remains on a road.
        for area in areas:
            if str(area.get("type","")).upper()=="LAKE" and water_override:
                continue
            forbidden={str(x).upper() for x in area.get("impassable_mobility_classes",[])}
            if mobility in forbidden and not self.on_road(p):
                return False
            ov=dict(area.get("mobility_overrides",{}))
            if mobility in ov and float(ov[mobility])<=0.0 and not self.on_road(p):
                return False
        river=self.river_at(p)
        if river is None: return True
        if self.on_bridge(p): return True
        ov=dict(river.get("mobility_overrides",{}))
        if mobility in ov:
            return float(ov[mobility])>0.0
        caps={str(x).upper() for x in md.get("mobility_capabilities",[])}
        return bool(caps & {"AMPHIBIOUS","WATER_CROSSING"})

    def segment_passable(self,unit,a:Vec2,b:Vec2)->bool:
        """Shared continuous hard-terrain check for planning and actual movement.

        Evaluate the same point rules at every membership boundary and inside
        each resulting interval. Roads/bridges are exceptions only where their
        entire necessary corridor is present. No fixed-distance sampling.
        """
        if a!=b and self.segment_crosses_barricade(a,b):return False
        areas,water_restricted=movement_regions(self,unit)
        if not areas and not water_restricted:return True
        if not self.passable(unit,a) or not self.passable(unit,b):return False
        if a==b:return True
        cuts=movement_cuts(self,a,b,areas,water_restricted)
        def point(t):return (a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t)
        for t in cuts[1:-1]:
            if not self.passable(unit,point(t)):return False
        return all(self.passable(unit,point((lo+hi)/2)) for lo,hi in zip(cuts,cuts[1:]) if hi>lo)

    def plan_route(self, unit, dest:Vec2) -> List[Vec2]:
        """Return a sparse A* route optimized primarily for estimated travel time.

        Road vertices and operational bridge portals form the navigation graph. Unit-specific
        terrain speed factors influence route choice without hard-coded branch names. Future
        doctrine can add risk/mine/cover costs through the planner policy interface.
        """
        return self.navigation.plan(unit, tuple(unit.pos), tuple(dest))

    def movement_target(self,unit,dest:Vec2)->Vec2:
        """Return the next A* navigation waypoint toward *dest*."""
        return self.navigation.next_waypoint(unit, tuple(dest))
