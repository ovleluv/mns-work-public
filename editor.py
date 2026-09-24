from __future__ import annotations
import json, math, os, argparse, csv
from pathlib import Path
import pygame
from mnsim.symbology import echelon_amplifier, branch_symbol_kind
from mnsim.cartography import contour_label_candidate

W,H=1500,900
MIN_W,MIN_H=960,640
PANEL_MIN_W,PANEL_MAX_W=330,460
MAP=pygame.Rect(0,0,1160,900); PANEL=pygame.Rect(1160,0,340,900)
BG=(218,218,191); PANELC=(31,37,42); TEXT=(230,233,235); MUTED=(160,168,172)
BLUE=(35,81,232); RED=(235,48,54); WATER=(178,206,211); ROAD=(141,133,112)
AREA_COLORS={'WOODS':(116,137,95),'FOREST':(82,111,70),'BRUSH':(143,151,104),'URBAN':(168,164,153),'OPEN':(211,214,185),'LAKE':(112,166,205),'BUILDING':(126,122,118),'ELEVATION':(205,190,154)}
BARRICADE_COLOR=(112,91,58)
TOOLS=['SELECT','UNIT','ROAD','RIVER','BRIDGE','WOODS','FOREST','BRUSH','URBAN','LAKE','BUILDING','ELEVATION','BARRICADE']
SIDES=['BLUE','RED']; ECHELONS=['IND','SQD','PLT','COY','BN']
ECHELON_NAMES={'IND':'Individual','SQD':'Squad','PLT':'Platoon','COY':'Company','BN':'Battalion'}
POLYGON_TOOLS={'WOODS','FOREST','BRUSH','URBAN','LAKE','BUILDING','ELEVATION'}  # legacy: POLYGON_TOOLS={'WOODS','FOREST','BRUSH','URBAN'}
POLYGON_CLOSE_RADIUS_PX=14
SELECT_RADIUS_PX=18

def point_segment_distance(px,py,ax,ay,bx,by):
 dx=bx-ax; dy=by-ay
 if dx==0 and dy==0:return math.hypot(px-ax,py-ay)
 t=((px-ax)*dx+(py-ay)*dy)/(dx*dx+dy*dy)
 t=max(0.0,min(1.0,t)); qx=ax+t*dx; qy=ay+t*dy
 return math.hypot(px-qx,py-qy)

def point_in_poly(p,poly):
 x,y=p;inside=False;j=len(poly)-1
 for i in range(len(poly)):
  xi,yi=poly[i];xj,yj=poly[j]
  if ((yi>y)!=(yj>y)) and (x < (xj-xi)*(y-yi)/(yj-yi+1e-12)+xi):inside=not inside
  j=i
 return inside

def nice_metric_distance(raw_m):
 raw=max(1e-9,float(raw_m)); exp=10.0**math.floor(math.log10(raw)); frac=raw/exp
 if frac<1.5:nice=1.0
 elif frac<3.5:nice=2.0
 elif frac<7.5:nice=5.0
 else:nice=10.0
 return nice*exp

def format_metric(m):
 return f'{m/1000:g} km' if m>=1000 else f'{m:g} m'


class Editor:
 def __init__(self,scenario=None):
  self.root=Path(__file__).resolve().parent
  self.scenario_path=None; self.terrain_path=None
  self.scenario={}; self.terrain={'roads':[],'rivers':[],'bridges':[],'barricades':[],'areas':[]}; self.types=[]; self.type_defs={}; self.platform_defs={}; self.weapon_defs={}; self.tool='SELECT'; self.side='BLUE'; self.echelon='PLT'; self.type_idx=0
  self.selected=None; self.points=[]; self.river_width=140.; self.road_width=36.; self.bridge_width=80.; self.elevation_m=50.0; self.building_height_m=8.0; self.barricade_heading_deg=0.0
  self.comp_draft=None; self.comp_scroll=0; self.comp_buttons=[]; self.template_buttons=[]
  self.delete_undo=[]
  self.cam_x=2000.0; self.cam_y=2000.0; self.zoom=1.0
  self.message='Blank 4 km x 4 km plain | mouse wheel zoom | arrows pan'
  if scenario:
   path=(self.root/scenario).resolve() if not Path(scenario).is_absolute() else Path(scenario)
   self.load(path)
  else:
   self.new_blank()

 def update_layout(self,size):
  """Recompute the editor viewport/panel geometry from the live window size.

  MAP/PANEL remain module-level rectangles for backward compatibility with the
  existing coordinate helpers, but their geometry is no longer fixed.
  """
  global W,H,MAP,PANEL
  w=max(MIN_W,int(size[0])); h=max(MIN_H,int(size[1]))
  W,H=w,h
  # Keep the inspector genuinely usable instead of shrinking it until labels overlap.
  target=int(w*0.27)
  if w<1150: target=int(w*0.34)
  panel_w=max(PANEL_MIN_W,min(PANEL_MAX_W,target))
  # Preserve a useful tactical viewport even at the minimum supported width.
  panel_w=min(panel_w,max(PANEL_MIN_W,w-560))
  MAP=pygame.Rect(0,0,w-panel_w,h)
  PANEL=pygame.Rect(MAP.right,0,panel_w,h)
  self.clamp_camera()

 def make_fonts(self):
  # Font sizes are bounded for readability; shrinking the window rearranges content
  # instead of reducing text to illegible sizes.
  scale=max(0.90,min(1.25,min(W/1500.0,H/900.0)))
  title=max(20,min(25,round(22*scale)))
  body=max(15,min(18,round(16*scale)))
  small=max(14,min(17,round(15*scale)))
  return (pygame.font.SysFont('arial',title,bold=True),
          pygame.font.SysFont('arial',body),
          pygame.font.SysFont('arial',small))

 @staticmethod
 def fit_text(font,text,max_width):
  """Return text clipped with an ellipsis to fit a single UI row."""
  text=str(text)
  if font.size(text)[0]<=max_width:return text
  ell='…'
  lo,hi=0,len(text)
  while lo<hi:
   mid=(lo+hi+1)//2
   if font.size(text[:mid]+ell)[0]<=max_width:lo=mid
   else:hi=mid-1
  return text[:lo]+ell

 @staticmethod
 def wrap_text(font,text,max_width):
  words=str(text).split()
  if not words:return ['']
  lines=[]; cur=words[0]
  for word in words[1:]:
   trial=cur+' '+word
   if font.size(trial)[0]<=max_width:cur=trial
   else:lines.append(cur);cur=word
  lines.append(cur)
  return lines

 def _load_weapon_defs(self):
  self.weapon_defs={}
  p=self.root/'database/weapons.csv'
  if not p.exists(): return
  with p.open('r',encoding='utf-8-sig',newline='') as f:
   for r in csv.DictReader(f):
    wid=str(r.get('weapon_id','')).strip()
    if not wid: continue
    try: md=json.loads(r.get('metadata_json') or '{}')
    except Exception: md={}
    self.weapon_defs[wid]={'name':r.get('name') or wid,'ammo':int(float(r.get('ammo') or -1)),'metadata':md}

 def _load_platform_defs(self):
  self.platform_defs={}
  p=self.root/'database/platforms.csv'
  if not p.exists(): return
  with p.open('r',encoding='utf-8-sig',newline='') as f:
   for r in csv.DictReader(f):
    pid=str(r.get('platform_id','')).strip()
    if pid:self.platform_defs[pid]={'crew':int(r.get('crew') or 0),'passengers':int(r.get('passengers') or 0)}

 def _enrich_platform_metadata(self):
  if not self.platform_defs:self._load_platform_defs()
  for t in self.type_defs.values():
   for e in t.get('elements',[]):
    pid=e.get('platform_id')
    if pid in self.platform_defs:
     md=e.setdefault('metadata',{})
     for k,v in self.platform_defs[pid].items():md.setdefault(k,v)

 def _load_default_types(self):
  toe=self.root/'config/toe_templates.json'
  if toe.exists():
   self.type_defs=dict(json.loads(toe.read_text(encoding='utf-8')).get('unit_types',{})); self._enrich_platform_metadata(); self._load_weapon_defs()
   self.types=list(self.type_defs.keys())
  else:
   self.type_defs={}; self.types=[]

 def compatible_types(self,echelon=None):
  """Return TO&E templates that actually belong to the requested echelon.

  Older editor versions treated echelon as a NATO-symbol-only property and allowed e.g.
  BN + INF_PLT.  Formation template metadata is now authoritative, so the editor cannot
  silently create a battalion symbol backed by platoon strength.
  """
  ech=str(echelon or self.echelon).upper()
  out=[name for name,t in self.type_defs.items() if str(t.get('metadata',{}).get('echelon','')).upper()==ech]
  return out or list(self.types)

 def current_type(self):
  choices=self.compatible_types()
  if not choices:return '-'
  self.type_idx%=len(choices)
  return choices[self.type_idx]

 def cycle_echelon(self):
  # Preserve the branch/family while moving PLT -> COY -> BN whenever possible.
  old=self.current_type()
  old_def=self.type_defs.get(old,{})
  family=str(old_def.get('metadata',{}).get('formation_family',''))
  branch=str(old_def.get('branch',''))
  self.echelon=ECHELONS[(ECHELONS.index(self.echelon)+1)%len(ECHELONS)]
  choices=self.compatible_types()
  idx=next((i for i,n in enumerate(choices) if str(self.type_defs.get(n,{}).get('metadata',{}).get('formation_family',''))==family),None)
  if idx is None:
   idx=next((i for i,n in enumerate(choices) if str(self.type_defs.get(n,{}).get('branch',''))==branch),0)
  self.type_idx=idx
  self.message=f'Echelon: {self.echelon} ({ECHELON_NAMES.get(self.echelon,self.echelon)}) | TO&E: {self.current_type()}'

 def new_blank(self):
  """Start an unsaved, empty 4 km x 4 km OPEN map.

  Configuration/TO&E references are attached only when the scenario is saved, so the initial
  editor canvas is terrain- and unit-free rather than an implicit copy of demo.json.
  """
  self.scenario={
   'seed':7,
   'world':{'width_m':4000,'height_m':4000},
   'objectives':{},
   'units':[],
   'aggregations':[]
  }
  self.terrain={'roads':[],'rivers':[],'bridges':[],'barricades':[],'areas':[]}
  self.scenario_path=None; self.terrain_path=None
  self._load_default_types()
  self.type_idx=min(self.type_idx,max(0,len(self.types)-1)); self.selected=None; self.comp_draft=None; self.points=[]; self.delete_undo=[]
  self.reset_camera()
  self.message='NEW blank plain | N new | O open | S save'

 def _confine_terrain_path(self):
  """Never write terrain outside the scenario's own folder tree.

  ``terrain_file`` comes from the opened scenario and is untrusted: "../../x" or an absolute
  path would otherwise make Save overwrite an arbitrary file.  Such references are redirected
  to ``<scenario>_terrain.json`` beside the scenario.  Returns True when redirected.
  """
  if self.scenario_path is None:
   return False
  base=self.scenario_path.resolve().parent
  target=Path(self.terrain_path).resolve() if self.terrain_path is not None else None
  inside=False
  if target is not None and target.suffix.lower()=='.json' and target!=self.scenario_path.resolve():
   try:
    target.relative_to(base); inside=True
   except ValueError:
    inside=False
  if inside:
   return False
  self.terrain_path=self.scenario_path.with_name(self.scenario_path.stem+'_terrain.json')
  return True

 def _prepare_save_references(self):
  if self.scenario_path is None or self.terrain_path is None:
   return
  # Standard engine resources are intentionally NOT serialized as filesystem paths.
  # This keeps scenario maps portable across version folders.  The current engine
  # supplies its own defaults/TO&E/doctrine; only the map-local terrain stays referenced.
  for key in ('config_file','unit_types_file','artillery_doctrine_file','targeting_doctrine_file'):
   self.scenario.pop(key,None)
  self.scenario['terrain_file']=os.path.relpath(self.terrain_path,self.scenario_path.parent).replace('\\','/')

 def world_size(self):
  w=self.scenario.get('world',{}); return float(w.get('width_m',4000)),float(w.get('height_m',4000))

 def base_scale(self):
  ww,wh=self.world_size()
  return min(MAP.w/max(1.0,ww),MAP.h/max(1.0,wh))

 def view_scale(self):
  return self.base_scale()*self.zoom

 def visible_world(self):
  sc=max(self.view_scale(),1e-9)
  return MAP.w/sc,MAP.h/sc

 def reset_camera(self):
  ww,wh=self.world_size()
  self.cam_x=ww/2.0; self.cam_y=wh/2.0; self.zoom=1.0

 def clamp_camera(self):
  ww,wh=self.world_size(); vw,vh=self.visible_world()
  if vw>=ww:self.cam_x=ww/2.0
  else:self.cam_x=max(vw/2.0,min(ww-vw/2.0,self.cam_x))
  if vh>=wh:self.cam_y=wh/2.0
  else:self.cam_y=max(vh/2.0,min(wh-vh/2.0,self.cam_y))

 def w2s(self,p):
  sc=self.view_scale()
  return (int(MAP.centerx+(p[0]-self.cam_x)*sc),int(MAP.centery+(p[1]-self.cam_y)*sc))

 def s2w(self,p):
  sc=max(self.view_scale(),1e-9)
  return (self.cam_x+(p[0]-MAP.centerx)/sc,self.cam_y+(p[1]-MAP.centery)/sc)

 def clamp_world_point(self,p):
  """Project a world-space point onto the rectangular map bounds.

  A point outside one side is snapped to that side while preserving the other
  coordinate.  A point outside two sides is therefore snapped to the nearest
  map corner.  This prevents terrain vertices drawn in letterbox/pillarbox
  space from extending beyond the playable world.
  """
  ww,wh=self.world_size()
  return (max(0.0,min(ww,float(p[0]))),max(0.0,min(wh,float(p[1]))))

 def clamp_terrain_to_world(self):
  """Clamp every terrain geometry point to the current world rectangle."""
  def cp(p):
   q=self.clamp_world_point(p)
   return [round(q[0],1),round(q[1],1)]
  for area in self.terrain.get('areas',[]):
   area['polygon']=[cp(p) for p in area.get('polygon',[])]
  for road in self.terrain.get('roads',[]):
   road['points']=[cp(p) for p in road.get('points',[])]
  for river in self.terrain.get('rivers',[]):
   river['points']=[cp(p) for p in river.get('points',[])]
  for bridge in self.terrain.get('bridges',[]):
   if bridge.get('points'):
    bridge['points']=[cp(p) for p in bridge.get('points',[])]
    if bridge['points']:
     bridge['center']=[round(sum(p[0] for p in bridge['points'])/len(bridge['points']),1),round(sum(p[1] for p in bridge['points'])/len(bridge['points']),1)]
   elif 'center' in bridge: bridge['center']=cp(bridge['center'])
  for wall in self.terrain.get('barricades',[]):
   if 'center' in wall:wall['center']=cp(wall['center'])

 def zoom_at(self,screen_pos,wheel_y):
  """Zoom around the cursor so the world point under the mouse stays fixed."""
  before=self.s2w(screen_pos)
  factor=1.20 if wheel_y>0 else 1/1.20
  self.zoom=max(1.0,min(20.0,self.zoom*factor))
  after=self.s2w(screen_pos)
  self.cam_x+=before[0]-after[0]; self.cam_y+=before[1]-after[1]
  self.clamp_camera()

 def pan(self,dx_screen,dy_screen):
  sc=max(self.view_scale(),1e-9)
  self.cam_x+=dx_screen/sc; self.cam_y+=dy_screen/sc
  self.clamp_camera()

 def load(self,path):
  path=Path(path); self.scenario=json.loads(path.read_text(encoding='utf-8')); self.scenario_path=path
  tref=self.scenario.get('terrain_file'); tp=(path.parent/tref).resolve() if tref else None
  self.terrain=json.loads(tp.read_text(encoding='utf-8')) if tp and tp.exists() else {'roads':[],'rivers':[],'bridges':[],'barricades':[],'areas':[]}; self.terrain.setdefault('barricades',[]); self.terrain_path=tp or path.with_name(path.stem+'_terrain.json')
  uref=self.scenario.get('unit_types_file'); up=(path.parent/uref).resolve() if uref else None
  if up and up.exists():
   self.type_defs=dict(json.loads(up.read_text(encoding='utf-8')).get('unit_types',{}))
  elif self.scenario.get('unit_types'):
   self.type_defs=dict(self.scenario.get('unit_types',{}))
  else:
   # Backward compatibility: old maps often carry a version-folder-relative TO&E path.
   # If that reference is stale after the map is moved, render against this release's TO&E.
   toe=self.root/'config/toe_templates.json'
   self.type_defs=dict(json.loads(toe.read_text(encoding='utf-8')).get('unit_types',{})) if toe.exists() else {}
  self._enrich_platform_metadata(); self._load_weapon_defs(); self.types=list(self.type_defs.keys())
  self.type_idx=min(self.type_idx,max(0,len(self.types)-1)); self.selected=None; self.comp_draft=None; self.points=[]; self.delete_undo=[]
  self.clamp_terrain_to_world()
  self.reset_camera()
 def set_map_size_dialog(self):
  try:
   import tkinter as tk
   from tkinter import simpledialog
   r=tk.Tk(); r.withdraw(); r.attributes('-topmost',True)
   ww,wh=self.world_size()
   w=simpledialog.askfloat('Map size','Width (metres):',initialvalue=ww,minvalue=100.0,maxvalue=200000.0,parent=r)
   if w is None: r.destroy(); return
   h=simpledialog.askfloat('Map size','Height (metres):',initialvalue=wh,minvalue=100.0,maxvalue=200000.0,parent=r)
   r.destroy()
   if h is None:return
   self.scenario.setdefault('world',{})['width_m']=float(w)
   self.scenario.setdefault('world',{})['height_m']=float(h)
   self.selected=None; self.points=[]
   self.clamp_terrain_to_world()
   self.reset_camera()
   self.message=f'Map resized: {format_metric(w)} x {format_metric(h)}'
  except Exception as ex:
   self.message=f'Map-size dialog unavailable: {ex}'

 def save(self):
  if self.scenario_path is None:
   chosen=self.choose(True)
   if not chosen:
    self.message='Save cancelled'; return
   self.scenario_path=Path(chosen)
   self.terrain_path=self.scenario_path.with_name(self.scenario_path.stem+'_terrain.json')
  redirected=self._confine_terrain_path()
  self._prepare_save_references()
  self.clamp_terrain_to_world()
  self.scenario_path.parent.mkdir(parents=True,exist_ok=True); self.terrain_path.parent.mkdir(parents=True,exist_ok=True)
  self.scenario_path.write_text(json.dumps(self.scenario,indent=2)+'\n',encoding='utf-8'); self.terrain_path.write_text(json.dumps(self.terrain,indent=2)+'\n',encoding='utf-8'); self.message=f'Saved {self.scenario_path.name} + {self.terrain_path.name}'+(' (terrain redirected into the scenario folder)' if redirected else '')
 def choose(self,save=False):
  try:
   import tkinter as tk; from tkinter import filedialog
   r=tk.Tk(); r.withdraw(); r.attributes('-topmost',True)
   initial=self.scenario_path.parent if self.scenario_path is not None else (self.root/'scenarios')
   p=(filedialog.asksaveasfilename if save else filedialog.askopenfilename)(initialdir=str(initial),defaultextension='.json',filetypes=[('JSON','*.json')])
   r.destroy(); return p
  except Exception: return None
 def uid(self,prefix,items):
  used={str(x.get('id','')) for x in items}; n=1
  while f'{prefix}{n}' in used:n+=1
  return f'{prefix}{n}'
 def add_unit(self,p):
  typ=self.current_type()
  if typ=='-':return
  td=self.type_defs.get(typ,{});branch=str(td.get('branch','')).upper();mobility=str(td.get('metadata',{}).get('mobility_class','FOOT')).upper()
  building=next((a for a in self.terrain.get('areas',[]) if str(a.get('type','')).upper()=='BUILDING' and point_in_poly(p,a.get('polygon',[]))),None)
  lake=next((a for a in self.terrain.get('areas',[]) if str(a.get('type','')).upper()=='LAKE' and point_in_poly(p,a.get('polygon',[]))),None)
  if building is not None and mobility!='FOOT':
   self.message='Only dismounted/foot infantry may be placed inside a building';return
  if lake is not None and not (mobility=='FOOT' and branch in ('INFANTRY','RECON','SPECIAL_OPERATIONS')):
   self.message='Only foot infantry may be initially placed in a lake';return
  uid=self.uid(('B-' if self.side=='BLUE' else 'R-')+typ+'-',self.scenario.setdefault('units',[]))
  self.scenario['units'].append({'id':uid,'name':uid,'side':self.side,'echelon':self.echelon,'type':typ,'pos':[round(p[0],1),round(p[1],1)],'heading_deg':0.0,'watch_heading_deg':0.0,'metadata':{'barricade_limit':0},'orders':[{'id':uid+'-HOLD','kind':'HOLD','params':{'duration_s':9999}}]})
 def _unit_override(self,u,eid):
  ovs=u.setdefault('element_overrides',[])
  ov=next((x for x in ovs if x.get('id')==eid),None)
  if ov is None:
   ov={'id':eid}; ovs.append(ov)
  return ov

 def toggle_optional_attachment(self):
  """Toggle the first editor-configurable optional attachment on the selected unit.

  The editor reads optional-attachment metadata from the TO&E instead of hard-coding INF_PLT or
  machine-gun element IDs. Future mortar/recon/EW attachments can reuse the same contract.
  """
  if not self.selected or self.selected[0]!='unit':
   self.message='Select a unit first'; return
  u=self.selected[1]; typ=self.type_defs.get(u.get('type'),{})
  opts=[e for e in typ.get('elements',[]) if e.get('metadata',{}).get('optional_attachment')]
  if not opts:
   self.message='Selected unit has no optional TO&E attachment'; return
  e=opts[0]; cfg=e.get('metadata',{}).get('optional_attachment',{})
  present=int(cfg.get('present_count',max(1,int(e.get('count',1)))))
  # When the composition editor is open, G edits the draft rather than mutating the
  # scenario behind the SAVE/CANCEL transaction boundary.
  if self.comp_draft:
   row=next((r for r in self.comp_draft['rows'] if r.get('id')==e.get('id')),None)
   if row is not None:
    row['count']=0 if int(row.get('count',0))>0 else present
    self.message=f"{cfg.get('label',e.get('name',e['id']))}: {'ON' if row['count'] else 'OFF'} (draft)"; return
  ov=self._unit_override(u,e['id']); current=int(ov.get('count',e.get('count',0)))
  new_count=0 if current>0 else present; ov['count']=new_count; ov['initial_count']=new_count
  self.message=f"{cfg.get('label',e.get('name',e['id']))}: {'ON' if new_count else 'OFF'}"

 def select_object(self,obj):
  self.selected=obj; self.comp_scroll=0
  self.comp_draft=self._make_composition_draft(obj[1]) if obj and obj[0]=='unit' else None

 def _weapon_key(self,w):
  return str(w.get('weapon_id') or w.get('slot') or w.get('name') or 'weapon')

 def _make_composition_draft(self,u):
  typ=self.type_defs.get(u.get('type'),{})
  ovs={x.get('id'):x for x in u.get('element_overrides',[])}
  rows=[]
  for e in typ.get('elements',[]):
   ov=ovs.get(e.get('id'),{})
   base_md=dict(e.get('metadata',{})); md=dict(base_md); md.update(dict(ov.get('metadata',{})))
   row={'id':e.get('id'),'name':e.get('name',e.get('id')),'role':e.get('role',''),
        'category':e.get('category','PERSONNEL'),'count':int(ov.get('count',e.get('count',0))),
        'initial_count':int(ov.get('initial_count',ov.get('count',e.get('initial_count',e.get('count',0))))),
        'min_operators':int(ov.get('min_operators',e.get('min_operators',1))),'weapons':[],
        'platform_id':e.get('platform_id'),'crew_per_vehicle':int(md.get('crew',0)),
        'passengers_per_vehicle':int(md.get('passengers',0)),
        'dismountable':bool(md.get('dismountable',False))}
   sysov=dict(ov.get('weapon_system_counts',{})); opsov=dict(ov.get('weapon_operators_per_system',{}))
   for w in e.get('weapons',[]):
    md=dict(w.get('metadata',{})); key=self._weapon_key(w); wid=str(w.get('weapon_id') or '')
    catalog=dict(getattr(self,'weapon_defs',{}).get(wid,{})); cmd=dict(catalog.get('metadata',{})); cmd.update(md)
    inv=str(cmd.get('inventory_model','')).upper()
    explicit=('system_count' in md) or inv=='DISPOSABLE_ROUNDS'
    wc=int(sysov.get(key,md.get('system_count',1)))
    op=int(opsov.get(key,md.get('operators_per_system',1)))
    default_ammo=w.get('ammo',catalog.get('ammo',-1))
    ammo=int(ov.get('weapon_ammo',{}).get(key,default_ammo if default_ammo is not None else -1))
    if inv=='DISPOSABLE_ROUNDS': wc=max(0,ammo)
    row['weapons'].append({'key':key,'weapon_id':wid,'label':str(w.get('slot') or wid or key),
                           'count':wc,'operators_per_system':op,'explicit':explicit,'ammo':ammo,
                           'inventory_model':inv,'quantity_label':str(cmd.get('editor_quantity_label','systems')),
                           'show_crew':bool(cmd.get('editor_show_crew',inv=='CREW_SERVED')),
                           'show_ammo':bool(cmd.get('editor_show_ammo',ammo>=0 and inv!='DISPOSABLE_ROUNDS'))})
   rows.append(row)
  return {'unit_id':u.get('id'),'barricade_limit':max(0,int(u.get('metadata',{}).get('barricade_limit',0))),'rows':rows}

 def save_composition_draft(self):
  if not self.selected or self.selected[0]!='unit' or not self.comp_draft:return
  u=self.selected[1]; typ=self.type_defs.get(u.get('type'),{}); byid={e.get('id'):e for e in typ.get('elements',[])}
  ovs=[]
  for r in self.comp_draft['rows']:
   base=byid.get(r['id'],{}); ov={'id':r['id'],'count':int(r['count']),'initial_count':int(r['count'])}
   if int(r.get('min_operators',1)) != int(base.get('min_operators',1)): ov['min_operators']=int(r['min_operators'])
   md={}
   if r.get('category','').upper()=='EQUIPMENT' and r.get('platform_id'):
    md['crew']=max(0,int(r.get('crew_per_vehicle',0))); md['passengers']=max(0,int(r.get('passengers_per_vehicle',0)))
   if r.get('category','').upper()=='PERSONNEL' and r.get('dismountable'): md['dismountable']=True
   if md: ov['metadata']=md
   sc={}; ops={}; ammo={}
   for w in r.get('weapons',[]):
    if w.get('explicit'):
     if str(w.get('inventory_model','')).upper()=='DISPOSABLE_ROUNDS':
      ammo[w['key']]=max(0,int(w.get('count',0)))
     else:
      sc[w['key']]=int(w['count'])
      if w.get('show_crew'): ops[w['key']]=int(w['operators_per_system'])
      if int(w.get('ammo',-1))>=0 and w.get('show_ammo'): ammo[w['key']]=int(w['ammo'])
   if sc:ov['weapon_system_counts']=sc
   if ops:ov['weapon_operators_per_system']=ops
   if ammo:ov['weapon_ammo']=ammo
   ovs.append(ov)
  u['element_overrides']=ovs
  u.setdefault('metadata',{})['barricade_limit']=max(0,int(self.comp_draft.get('barricade_limit',0)))
  self.comp_draft=self._make_composition_draft(u)
  self.message=f"Composition saved for {u.get('id')}"

 def cancel_composition_draft(self):
  if self.selected and self.selected[0]=='unit': self.comp_draft=self._make_composition_draft(self.selected[1])
  self.message='Composition edits cancelled'

 def adjust_composition(self,kind,row_idx,weapon_idx,delta):
  if not self.comp_draft:return
  if kind=='barricades':
   self.comp_draft['barricade_limit']=max(0,min(99,int(self.comp_draft.get('barricade_limit',0))+delta));return
  rows=self.comp_draft['rows']
  if not (0<=row_idx<len(rows)):return
  r=rows[row_idx]
  if kind=='count':
   r['count']=max(0,min(999,int(r['count'])+delta))
   for w in r.get('weapons',[]):
    inv=str(w.get('inventory_model','')).upper()
    if inv in ('INDIVIDUAL_ASSIGNED','PLATFORM_MOUNT'):
     w['count']=min(int(w.get('count',0)),int(r['count']))
  elif kind=='minop': r['min_operators']=max(1,min(99,int(r['min_operators'])+delta))
  elif kind=='crew': r['crew_per_vehicle']=max(0,min(20,int(r.get('crew_per_vehicle',0))+delta))
  elif kind=='seats': r['passengers_per_vehicle']=max(0,min(50,int(r.get('passengers_per_vehicle',0))+delta))
  elif kind in ('wcount','wops','wammo') and 0<=weapon_idx<len(r['weapons']):
   w=r['weapons'][weapon_idx]
   if kind=='wcount':
    inv=str(w.get('inventory_model','')).upper()
    w['count']=max(0,min(999 if inv=='DISPOSABLE_ROUNDS' else 99,int(w['count'])+delta))
    if inv=='DISPOSABLE_ROUNDS': w['ammo']=w['count']
    # One-person assigned weapons cannot exceed the personnel in their element.
    if inv=='INDIVIDUAL_ASSIGNED': w['count']=min(w['count'],max(0,int(r.get('count',0))))
    # Platform mounts cannot exceed the number of surviving/configured providers times mount capacity.
    if inv=='PLATFORM_MOUNT': w['count']=min(w['count'],max(0,int(r.get('count',0))))
   elif kind=='wammo':w['ammo']=max(0,min(999,int(w.get('ammo',0))+delta))
   else:w['operators_per_system']=max(1,min(99,int(w['operators_per_system'])+delta))

 def handle_panel_click(self,pos):
  for rect,idx in self.template_buttons:
   if rect.collidepoint(pos):
    choices=self.compatible_types()
    if 0<=idx<len(choices):
     self.type_idx=idx; self.tool='UNIT'
     self.message=f'Unit template: {self.current_type()} | click map to place'
    return True
  for rect,action in self.comp_buttons:
   if rect.collidepoint(pos):
    if action[0]=='save':self.save_composition_draft()
    elif action[0]=='cancel':self.cancel_composition_draft()
    else:self.adjust_composition(*action)
    return True
  return False

 def draw_unit_symbol(self,screen,u,q,tiny):
  col=BLUE if u.get('side')=='BLUE' else RED; x,y=q
  frame=pygame.Rect(x-15,y-10,30,20); pygame.draw.rect(screen,col,frame,2)
  typ=self.type_defs.get(u.get('type'),{}); branch=str(typ.get('branch','')).upper(); fam=str(typ.get('metadata',{}).get('formation_family','')).upper(); symbol_kind=branch_symbol_kind(branch)
  if symbol_kind in ('INFANTRY','MOTORIZED_INFANTRY'):
   pygame.draw.line(screen,col,(x-10,y-7),(x+10,y+7),2); pygame.draw.line(screen,col,(x-10,y+7),(x+10,y-7),2)
   if symbol_kind=='MOTORIZED_INFANTRY': pygame.draw.circle(screen,col,(x-8,y+12),2,1); pygame.draw.circle(screen,col,(x+8,y+12),2,1)
  elif symbol_kind=='MECH_INFANTRY':
   pygame.draw.ellipse(screen,col,pygame.Rect(x-10,y-5,20,10),2); pygame.draw.line(screen,col,(x-9,y-6),(x+9,y+6),2); pygame.draw.line(screen,col,(x-9,y+6),(x+9,y-6),2)
  elif symbol_kind=='ARMOR':pygame.draw.ellipse(screen,col,pygame.Rect(x-11,y-6,22,12),2)
  elif symbol_kind=='ARTILLERY':pygame.draw.circle(screen,col,(x,y),4)
  if fam=='MG': screen.blit(tiny.render('MG',True,col),(x-9,y-7))
  mark=echelon_amplifier(u.get('echelon'))
  if mark: screen.blit(tiny.render(mark,True,col),(x-tiny.size(mark)[0]//2,y-26))

 def should_close_polygon(self, screen_pos):
  """Close an area when the user clicks near its first vertex.

  The tolerance is deliberately screen-space based so the interaction remains consistent
  at every zoom level.  Three vertices must already exist, preventing premature closure.
  """
  if self.tool not in POLYGON_TOOLS or len(self.points)<3:
   return False
  first=self.w2s(self.points[0])
  return math.dist(screen_pos,first)<=POLYGON_CLOSE_RADIUS_PX

 def add_barricade(self,p):
  walls=self.terrain.setdefault('barricades',[]); bid=self.uid('HESCO',walls)
  walls.append({'id':bid,'type':'HESCO_MIL1','center':[round(p[0],1),round(p[1],1)],'heading_deg':self.barricade_heading_deg%360.0,
                'length_m':10.0,'width_m':1.06,'small_arms_cover_factor':0.40,'heavy_direct_cover_factor':0.70})
  self.message=f'Placed {bid} | facing {self.barricade_heading_deg%360:.0f}° | protected side is opposite'

 @staticmethod
 def barricade_endpoints(wall):
  cx,cy=map(float,wall.get('center',(0,0)));h=math.radians(float(wall.get('heading_deg',0)));half=float(wall.get('length_m',10))/2
  tx,ty=-math.sin(h),math.cos(h)
  return ((cx-tx*half,cy-ty*half),(cx+tx*half,cy+ty*half))

 def finish_shape(self):
  if self.tool=='BRIDGE' and len(self.points)>=2:
   pts=self.points[:]
   center=[round(sum(p[0] for p in pts)/len(pts),1),round(sum(p[1] for p in pts)/len(pts),1)]
   self.terrain.setdefault('bridges',[]).append({'id':self.uid('BR',self.terrain.get('bridges',[])),'points':pts,'center':center,'width_m':self.bridge_width,'structural_integrity':140.0})
  elif self.tool=='ROAD' and len(self.points)>=2:self.terrain.setdefault('roads',[]).append({'id':self.uid('R',self.terrain.get('roads',[])),'width_m':self.road_width,'points':self.points[:]})
  elif self.tool=='RIVER' and len(self.points)>=2:self.terrain.setdefault('rivers',[]).append({'id':self.uid('RV',self.terrain.get('rivers',[])),'width_m':self.river_width,'points':self.points[:]})
  elif self.tool in ('WOODS','FOREST','BRUSH','URBAN','LAKE','BUILDING','ELEVATION') and len(self.points)>=3:
   t=self.tool
   if t=='FOREST':
    area={'id':self.uid('F',self.terrain.get('areas',[])),'type':'FOREST','polygon':self.points[:],
          'movement_factor':0.45,'mobility_overrides':{'FOOT':0.45,'TRACKED':0.0,'WHEELED':0.0,'WHEELED_TOWED':0.0},
          'impassable_mobility_classes':['TRACKED','WHEELED','WHEELED_TOWED'],
          'observation_modifier':{'range_factor':0.88,'fov_factor':0.78,'awareness_factor':0.72,'detection_factor':0.45},
          'target_concealment_factor':0.34,
          'max_visual_penetration_m':75.0,'sensor_penetration_m':{'VISUAL':75.0,'THERMAL':105.0}}
   elif t in ('WOODS','BRUSH','URBAN'):
    defaults={'WOODS':(.82,.72,.68),'BRUSH':(.90,.82,.80),'URBAN':(.70,.55,.62)}[t]
    area={'id':self.uid(t[0],self.terrain.get('areas',[])),'type':t,'polygon':self.points[:],
          'movement_factor':defaults[0],
          'observation_modifier':{'range_factor':defaults[1],'fov_factor':.9,'awareness_factor':.9,'detection_factor':defaults[2]}}
    if t=='WOODS':
     area.update({'target_concealment_factor':0.52,'max_visual_penetration_m':180.0,
                  'sensor_penetration_m':{'VISUAL':180.0,'THERMAL':240.0}})
   elif t=='LAKE':
    area={'id':self.uid('LK',self.terrain.get('areas',[])),'type':'LAKE','polygon':self.points[:],
          'movement_factor':0.16,'mobility_overrides':{'FOOT':0.16,'TRACKED':0.0,'WHEELED':0.0,'WHEELED_TOWED':0.0},
          'impassable_mobility_classes':['TRACKED','WHEELED','WHEELED_TOWED']}
   elif t=='BUILDING':
    area={'id':self.uid('BLD',self.terrain.get('areas',[])),'type':'BUILDING','polygon':self.points[:],
          'structural_integrity':180.0,'max_integrity':180.0,'integrity':180.0,'height_m':self.building_height_m,
          'external_detection_factor':0.42,'external_range_factor':0.82,
          'external_direct_fire_factor':0.38}
   else:
    area={'id':self.uid('EL',self.terrain.get('areas',[])),'type':'ELEVATION','polygon':self.points[:],
          'elevation_m':self.elevation_m,'transition_width_m':80.0}
   self.terrain.setdefault('areas',[]).append(area)
  self.points=[]
 def nearest(self,p,screen_pos=None):
  """Pick the visually top-most editable object under the cursor.

  Hit tolerances are screen-space based so selecting thin roads/rivers and symbols
  behaves consistently at every zoom level.
  """
  sp=screen_pos or self.w2s(p); sx,sy=sp; sc=self.view_scale()
  # Units and bridges are rendered last, so they get first refusal.
  for u in reversed(self.scenario.get('units',[])):
   if math.dist(sp,self.w2s(u['pos']))<=SELECT_RADIUS_PX:return ('unit',u)
  for wall in reversed(self.terrain.get('barricades',[])):
   a,b=self.barricade_endpoints(wall);sa,sb=self.w2s(a),self.w2s(b)
   if point_segment_distance(sx,sy,*sa,*sb)<=max(7.0,4.0*sc):return ('barricade',wall)
  for b in reversed(self.terrain.get('bridges',[])):
   bpts=[self.w2s(q) for q in b.get('points',[])]
   if len(bpts)>=2:
    tol=max(7.0,float(b.get('width_m',80))*sc/2.0+5.0)
    if any(point_segment_distance(sx,sy,*a,*c)<=tol for a,c in zip(bpts,bpts[1:])):return ('bridge',b)
   elif 'center' in b and math.dist(sp,self.w2s(b['center']))<=SELECT_RADIUS_PX:return ('bridge',b)
  # Roads are drawn over rivers/areas. Use their rendered half-width plus a small grab margin.
  for kind,key in (('road','roads'),('river','rivers')):
   for obj in reversed(self.terrain.get(key,[])):
    pts=[self.w2s(q) for q in obj.get('points',[])]
    width_m=float(obj.get('width_m',36 if kind=='road' else 140))
    tol=max(7.0,width_m*sc/2.0+5.0)
    if any(point_segment_distance(sx,sy,*a,*b)<=tol for a,b in zip(pts,pts[1:])):
     return (kind,obj)
  # Filled areas are the bottom-most terrain layer. Reverse iteration favors the latest object.
  for a in reversed(self.terrain.get('areas',[])):
   pts=[self.w2s(q) for q in a.get('polygon',[])]
   if len(pts)>=3:
    # pygame has no direct point-in-polygon helper; ray casting keeps this dependency-free.
    inside=False; j=len(pts)-1
    for i in range(len(pts)):
     xi,yi=pts[i]; xj,yj=pts[j]
     if ((yi>sy)!=(yj>sy)) and (sx < (xj-xi)*(sy-yi)/(yj-yi+1e-12)+xi):inside=not inside
     j=i
    if inside:return ('area',a)
  return None
 def delete_selected(self):
  if not self.selected:return
  kind,obj=self.selected
  table={'unit':self.scenario.get('units',[]),'bridge':self.terrain.get('bridges',[]),'barricade':self.terrain.get('barricades',[]),
         'road':self.terrain.get('roads',[]),'river':self.terrain.get('rivers',[]),
         'area':self.terrain.get('areas',[])}
  seq=table.get(kind)
  if seq is None or obj not in seq:return
  idx=seq.index(obj); seq.pop(idx); self.delete_undo.append((kind,obj,idx))
  self.selected=None; self.message=f'Deleted {obj.get("id",kind)} | Ctrl+Z to restore'
 def undo_delete(self):
  if not self.delete_undo:
   self.message='Nothing to restore'; return
  kind,obj,idx=self.delete_undo.pop()
  table={'unit':self.scenario.get('units',[]),'bridge':self.terrain.get('bridges',[]),'barricade':self.terrain.get('barricades',[]),
         'road':self.terrain.get('roads',[]),'river':self.terrain.get('rivers',[]),
         'area':self.terrain.get('areas',[])}
  seq=table[kind]; seq.insert(min(idx,len(seq)),obj); self.selected=(kind,obj)
  self.message=f'Restored {obj.get("id",kind)}'
 def draw(self,screen,font,tiny):
  screen.fill((45,48,46)); pygame.draw.rect(screen,(45,48,46),MAP)
  old_clip=screen.get_clip(); screen.set_clip(MAP)
  ww,wh=self.world_size(); px_per_m=self.view_scale()
  # Draw only the actual authored world; rectangular maps retain their true aspect ratio.
  tl=self.w2s((0,0)); br=self.w2s((ww,wh))
  world_rect=pygame.Rect(tl[0],tl[1],br[0]-tl[0],br[1]-tl[1]); world_rect.normalize()
  world_clip=MAP.clip(world_rect)
  pygame.draw.rect(screen,BG,world_clip)
  # MAP includes any aspect-ratio letterbox.  Clip authored world geometry to the actual world
  # rectangle so wide rivers/roads and polygon fills cannot bleed into those dark margins.
  screen.set_clip(world_clip)
  # Dynamic metric grid based on current zoom; draw only visible grid lines.
  step=nice_metric_distance(95.0/max(px_per_m,1e-9))
  vw,vh=self.visible_world(); left=max(0.0,self.cam_x-vw/2); top=max(0.0,self.cam_y-vh/2)
  x=math.floor(left/step)*step
  while x<=ww+1e-6:
   pygame.draw.line(screen,(185,185,165),self.w2s((x,0)),self.w2s((x,wh)),1); x+=step
  y=math.floor(top/step)*step
  while y<=wh+1e-6:
   pygame.draw.line(screen,(185,185,165),self.w2s((0,y)),self.w2s((ww,y)),1); y+=step
  contour_labels=[]; occupied_contour_labels=[]
  for a in self.terrain.get('areas',[]):
   pts=[self.w2s(p) for p in a.get('polygon',[])];
   if len(pts)>=3:
    typ=str(a.get('type','')).upper()
    if typ=='ELEVATION':
     pygame.draw.polygon(screen,AREA_COLORS['ELEVATION'],pts,2)
     text=f"{a.get('elevation_m',0):g}m"; base=tiny.render(text,True,(100,82,52))
     cand=contour_label_candidate(pts,base.get_size(),occupied_contour_labels,6.0)
     if cand:
      center,angle,rect=cand;occupied_contour_labels.append(rect);contour_labels.append((base,center,angle))
    else:pygame.draw.polygon(screen,AREA_COLORS.get(typ,(190,190,175)),pts)
  for base,center,angle in contour_labels:
   surf=pygame.transform.rotate(base,-angle);r=surf.get_rect(center=(round(center[0]),round(center[1])))
   pygame.draw.rect(screen,(232,226,199),r.inflate(4,2),border_radius=2);screen.blit(surf,r)
  for rv in self.terrain.get('rivers',[]):
   pts=[self.w2s(p) for p in rv.get('points',[])];
   if len(pts)>=2:
    sc=px_per_m; width=max(2,int(rv.get('width_m',120)*sc)); pygame.draw.lines(screen,WATER,False,pts,width)
    for q in pts[1:-1]:pygame.draw.circle(screen,WATER,q,max(1,width//2))
  for r in self.terrain.get('roads',[]):
   pts=[self.w2s(p) for p in r.get('points',[])];
   if len(pts)>=2: pygame.draw.lines(screen,ROAD,False,pts,max(2,int(r.get('width_m',30)*px_per_m)))
  for wall in self.terrain.get('barricades',[]):
   a,b=self.barricade_endpoints(wall);sa,sb=self.w2s(a),self.w2s(b);c=self.w2s(wall.get('center',(0,0)))
   pygame.draw.line(screen,BARRICADE_COLOR,sa,sb,max(3,int(max(1.0,float(wall.get('width_m',1.06))*px_per_m))))
   h=math.radians(float(wall.get('heading_deg',0)));n=(math.cos(h),math.sin(h));tip=(int(c[0]+n[0]*15),int(c[1]+n[1]*15))
   pygame.draw.line(screen,(155,68,50),c,tip,2);pygame.draw.circle(screen,(155,68,50),tip,3)
  for b in self.terrain.get('bridges',[]):
   bpts=[self.w2s(p) for p in b.get('points',[])]
   if len(bpts)>=2:
    bw=max(3,int(float(b.get('width_m',80))*px_per_m)); pygame.draw.lines(screen,(115,100,75),False,bpts,bw)
    for q in bpts[1:-1]:pygame.draw.circle(screen,(115,100,75),q,max(1,bw//2))
    mid=bpts[len(bpts)//2]; screen.blit(tiny.render(b['id'],True,(30,30,30)),(mid[0]+8,mid[1]-8))
   else:
    c=self.w2s(b['center']); pygame.draw.rect(screen,(115,100,75),pygame.Rect(c[0]-13,c[1]-6,26,12)); screen.blit(tiny.render(b['id'],True,(30,30,30)),(c[0]+15,c[1]-8))
  for u in self.scenario.get('units',[]):
   q=self.w2s(u['pos']); self.draw_unit_symbol(screen,u,q,tiny)
   # watch_heading_deg is the formation's principal observation / engagement bearing, not NATO-symbol rotation.
   wh=float(u.get('watch_heading_deg',u.get('heading_deg',0.0)))%360.0; a=math.radians(wh)
   tip=(int(q[0]+math.cos(a)*28),int(q[1]+math.sin(a)*28))
   pygame.draw.line(screen,(35,125,145),q,tip,2); pygame.draw.circle(screen,(35,125,145),tip,3)
   screen.blit(tiny.render(u['id'],True,(30,30,30)),(q[0]+18,q[1]-8))
  # Selected-object overlay: bright outline without changing the underlying terrain data.
  if self.selected:
   kind,obj=self.selected; hi=(255,225,80)
   if kind=='unit':
    q=self.w2s(obj['pos']); pygame.draw.rect(screen,hi,(q[0]-17,q[1]-13,34,26),2)
   elif kind=='barricade':
    a,b=self.barricade_endpoints(obj);pygame.draw.line(screen,hi,self.w2s(a),self.w2s(b),5)
   elif kind=='bridge':
    bpts=[self.w2s(q) for q in obj.get('points',[])]
    if len(bpts)>=2:pygame.draw.lines(screen,hi,False,bpts,max(3,int(float(obj.get('width_m',80))*px_per_m)+4))
    else:
     q=self.w2s(obj['center']); pygame.draw.rect(screen,hi,(q[0]-18,q[1]-11,36,22),2)
   elif kind in ('road','river'):
    pts=[self.w2s(q) for q in obj.get('points',[])]
    if len(pts)>=2:pygame.draw.lines(screen,hi,False,pts,max(3,int(obj.get('width_m',30)*px_per_m)+4))
   elif kind=='area':
    pts=[self.w2s(q) for q in obj.get('polygon',[])]
    if len(pts)>=3:pygame.draw.polygon(screen,hi,pts,3)
  if len(self.points):
   pts=[self.w2s(p) for p in self.points]; pygame.draw.lines(screen,(255,210,70),False,pts,3) if len(pts)>1 else None
   for q in pts:pygame.draw.circle(screen,(255,210,70),q,4)
   if self.tool in POLYGON_TOOLS and len(pts)>=3:
    # Make the auto-close target visible; clicking anywhere inside this ring finishes the polygon.
    pygame.draw.circle(screen,(255,235,135),pts[0],POLYGON_CLOSE_RADIUS_PX,2)
  # Cartographic annotations belong to the editor viewport rather than the authored-world clip.
  screen.set_clip(MAP)
  # Cartographic scale bar independent of grid spacing.
  dist=nice_metric_distance(125.0/max(px_per_m,1e-9)); bar_px=max(24,int(dist*px_per_m))
  # Leave a full text-row gap at the upper-left so guidance/status annotations
  # cannot be covered by the cartographic scale; lower-left remains free for controls.
  label=format_metric(dist); _,label_h=tiny.size(label)
  bx=MAP.left+18; by=MAP.top+label_h+54
  tw,th=tiny.size(label); back=pygame.Rect(bx-6,by-th-16,max(bar_px+12,tw+12),th+23)
  pygame.draw.rect(screen,(235,236,226),back,border_radius=3); pygame.draw.rect(screen,(75,78,72),back,1,border_radius=3)
  pygame.draw.line(screen,(35,38,35),(bx,by),(bx+bar_px,by),3)
  pygame.draw.line(screen,(35,38,35),(bx,by-5),(bx,by+2),2); pygame.draw.line(screen,(35,38,35),(bx+bar_px,by-5),(bx+bar_px,by+2),2)
  screen.blit(tiny.render(label,True,(35,38,35)),(bx,by-th-8))
  grid_text=tiny.render(f'Grid {format_metric(step)}',True,(60,63,58))
  screen.blit(grid_text,(MAP.right-grid_text.get_width()-12,MAP.top+10))

  screen.set_clip(old_clip)
  # Responsive inspector panel.  All coordinates are derived from PANEL rather than
  # the original 1500x900 layout, so resizing cannot make labels/buttons overlap.
  pygame.draw.rect(screen,PANELC,PANEL)
  pad=max(12,min(20,PANEL.w//22)); x=PANEL.left+pad; right=PANEL.right-pad
  content_w=max(80,right-x); y=max(12,pad-2); self.comp_buttons=[]; self.template_buttons=[]
  title_h=font.get_linesize(); line_h=tiny.get_linesize()+3

  title=self.fit_text(font,'MAP & SCENARIO EDITOR',content_w)
  screen.blit(font.render(title,True,TEXT),(x,y)); y+=title_h+6

  # Keep shortcuts legible by wrapping them rather than shrinking the font.
  header_lines=[
   '1 SELECT   2 UNIT   3 ROAD   4 RIVER   5 BRIDGE',
   '6 WOODS   7 FOREST   8 BRUSH   9 URBAN',
   'L LAKE   K BUILDING   V ELEVATION   H BARRICADE',
   f'Tool: {self.tool}   Side [B/R]: {self.side}',
   f'Elev/Barricade [,/ .]: {self.elevation_m:g} m / {self.barricade_heading_deg%360:g}°   Building H [; / \']: {self.building_height_m:g} m',
   f'Echelon [E]: {self.echelon} ({ECHELON_NAMES.get(self.echelon,self.echelon)})',
   f'TO&E [Q/W]: {self.current_type()}',
  ]
  ww,wh=self.world_size()
  header_lines += [
   f'Map [M]: {format_metric(ww)} × {format_metric(wh)}   Zoom {self.zoom:.2f}×',
   'S Save   Shift+S Save As   O Open   N New',
   'Wheel: map zoom / composition scroll',
  ]
  for text in header_lines:
   for ln in self.wrap_text(tiny,text,content_w):
    screen.blit(tiny.render(ln,True,TEXT if text.startswith(('Tool:','Echelon','TO&E')) else MUTED),(x,y)); y+=line_h
   if text in (header_lines[1],header_lines[4]): y+=2
  y+=5

  # Reserve a persistent bottom status area and action bar.
  status_lines=self.wrap_text(tiny,self.message,content_w)[:2]
  status_h=max(line_h,len(status_lines)*line_h)+8
  bottom=PANEL.bottom-pad
  status_top=bottom-status_h

  if self.selected and self.selected[0]=='unit' and self.comp_draft:
   u=self.selected[1]
   screen.blit(tiny.render(self.fit_text(tiny,'SELECTED UNIT COMPOSITION',content_w),True,(255,210,70)),(x,y)); y+=line_h
   ident=f"{u.get('id')} | {u.get('type')} | {u.get('echelon')}"
   screen.blit(tiny.render(self.fit_text(tiny,ident,content_w),True,TEXT),(x,y)); y+=line_h
   wh=float(u.get('watch_heading_deg',u.get('heading_deg',0.0)))%360.0
   screen.blit(tiny.render(self.fit_text(tiny,f'Watch / engagement bearing: {wh:.0f} deg  [Ctrl+Left/Right]',content_w),True,(110,205,220)),(x,y)); y+=line_h
   screen.blit(tiny.render('Draft only until SAVE CHANGES',True,MUTED),(x,y)); y+=line_h+4
   # Capacity is an allocation ceiling, not dedicated engineer manpower. Default remains zero.
   cap_ctrl_w=max(86,min(118,int(content_w*0.34)));cap_x=right-cap_ctrl_w;cap_btn=max(23,min(28,line_h+5))
   # The label shares the row with the -/+ control; keep it clear of that control.
   screen.blit(tiny.render(self.fit_text(tiny,'Barricade capacity (10m MIL1 sections)',max(40,content_w-cap_ctrl_w-8)),True,MUTED),(x,y))
   m=pygame.Rect(cap_x,y-2,cap_btn,cap_btn);a=pygame.Rect(right-cap_btn,y-2,cap_btn,cap_btn)
   pygame.draw.rect(screen,(67,75,81),m,border_radius=4);pygame.draw.rect(screen,(67,75,81),a,border_radius=4)
   screen.blit(tiny.render('-',True,TEXT),(m.centerx-tiny.size('-')[0]//2,m.centery-tiny.size('-')[1]//2));screen.blit(tiny.render('+',True,TEXT),(a.centerx-tiny.size('+')[0]//2,a.centery-tiny.size('+')[1]//2))
   val=str(self.comp_draft.get('barricade_limit',0));screen.blit(tiny.render(val,True,(255,210,70)),((m.right+a.left-tiny.size(val)[0])//2,y))
   self.comp_buttons += [(m,('barricades',-1,-1,-1)),(a,('barricades',-1,-1,1))];y+=line_h+7

   action_h=max(34,line_h+10); action_gap=8
   action_y=status_top-action_gap-action_h
   clip_bottom=max(y,action_y-action_gap)
   clip=pygame.Rect(PANEL.left+pad,y,PANEL.w-2*pad,max(0,clip_bottom-y))
   old=screen.get_clip(); screen.set_clip(clip)

   row_h=max(26,line_h+5); sub_h=max(24,line_h+3)
   row_y=y-self.comp_scroll*row_h
   control_w=max(86,min(118,int(content_w*0.34))); ctrl_x=right-control_w
   btn=max(23,min(28,row_h-3)); number_gap=max(30,control_w-2*btn)
   label_w=max(70,ctrl_x-x-8)
   for ri,r in enumerate(self.comp_draft['rows']):
    if row_y>clip.top-row_h and row_y<clip.bottom:
     label=f"{r['name']} [{r['role']}]"
     screen.blit(tiny.render(self.fit_text(tiny,label,label_w),True,TEXT),(x,row_y))
     minus=pygame.Rect(ctrl_x,row_y-2,btn,btn); plus=pygame.Rect(right-btn,row_y-2,btn,btn)
     pygame.draw.rect(screen,(67,75,81),minus,border_radius=4); pygame.draw.rect(screen,(67,75,81),plus,border_radius=4)
     screen.blit(tiny.render('-',True,TEXT),(minus.centerx-tiny.size('-')[0]//2,minus.centery-tiny.size('-')[1]//2))
     screen.blit(tiny.render('+',True,TEXT),(plus.centerx-tiny.size('+')[0]//2,plus.centery-tiny.size('+')[1]//2))
     val=str(r['count']); screen.blit(tiny.render(val,True,(255,210,70)),((minus.right+plus.left-tiny.size(val)[0])//2,row_y))
     self.comp_buttons += [(minus,('count',ri,-1,-1)),(plus,('count',ri,-1,1))]
    row_y+=row_h
    if r.get('category','').upper()=='EQUIPMENT' and r.get('platform_id'):
     for field,label,action in [('crew_per_vehicle','  crew / vehicle','crew'),('passengers_per_vehicle','  passenger seats / vehicle','seats')]:
      if row_y>clip.top-sub_h and row_y<clip.bottom:
       screen.blit(tiny.render(self.fit_text(tiny,label,label_w),True,MUTED),(x,row_y))
       m=pygame.Rect(ctrl_x,row_y-2,btn,btn); a=pygame.Rect(right-btn,row_y-2,btn,btn)
       pygame.draw.rect(screen,(67,75,81),m,border_radius=4); pygame.draw.rect(screen,(67,75,81),a,border_radius=4)
       screen.blit(tiny.render('-',True,TEXT),(m.centerx-tiny.size('-')[0]//2,m.centery-tiny.size('-')[1]//2)); screen.blit(tiny.render('+',True,TEXT),(a.centerx-tiny.size('+')[0]//2,a.centery-tiny.size('+')[1]//2))
       val=str(r.get(field,0)); screen.blit(tiny.render(val,True,(255,210,70)),((m.right+a.left-tiny.size(val)[0])//2,row_y))
       self.comp_buttons += [(m,(action,ri,-1,-1)),(a,(action,ri,-1,1))]
      row_y+=sub_h
    for wi,w in enumerate(r.get('weapons',[])):
     if not w.get('explicit'): continue
     if row_y>clip.top-sub_h and row_y<clip.bottom:
      label=f"  {w['label']} {w.get('quantity_label','systems')}"
      screen.blit(tiny.render(self.fit_text(tiny,label,label_w),True,MUTED),(x,row_y))
      m=pygame.Rect(ctrl_x,row_y-2,btn,btn); a=pygame.Rect(right-btn,row_y-2,btn,btn)
      pygame.draw.rect(screen,(67,75,81),m,border_radius=4); pygame.draw.rect(screen,(67,75,81),a,border_radius=4)
      screen.blit(tiny.render('-',True,TEXT),(m.centerx-tiny.size('-')[0]//2,m.centery-tiny.size('-')[1]//2)); screen.blit(tiny.render('+',True,TEXT),(a.centerx-tiny.size('+')[0]//2,a.centery-tiny.size('+')[1]//2))
      val=str(w['count']); screen.blit(tiny.render(val,True,(255,210,70)),((m.right+a.left-tiny.size(val)[0])//2,row_y))
      self.comp_buttons += [(m,('wcount',ri,wi,-1)),(a,('wcount',ri,wi,1))]
     row_y+=sub_h
     if w.get('show_ammo') and int(w.get('ammo',-1))>=0:
      if row_y>clip.top-sub_h and row_y<clip.bottom:
       label=f"    ammo / rounds"
       screen.blit(tiny.render(self.fit_text(tiny,label,label_w),True,MUTED),(x,row_y))
       m=pygame.Rect(ctrl_x,row_y-2,btn,btn); a=pygame.Rect(right-btn,row_y-2,btn,btn)
       pygame.draw.rect(screen,(67,75,81),m,border_radius=4); pygame.draw.rect(screen,(67,75,81),a,border_radius=4)
       screen.blit(tiny.render('-',True,TEXT),(m.centerx-tiny.size('-')[0]//2,m.centery-tiny.size('-')[1]//2)); screen.blit(tiny.render('+',True,TEXT),(a.centerx-tiny.size('+')[0]//2,a.centery-tiny.size('+')[1]//2))
       val=str(w.get('ammo',0)); screen.blit(tiny.render(val,True,(255,210,70)),((m.right+a.left-tiny.size(val)[0])//2,row_y))
       self.comp_buttons += [(m,('wammo',ri,wi,-1)),(a,('wammo',ri,wi,1))]
      row_y+=sub_h
     if r.get('category','').upper()=='PERSONNEL' and w.get('show_crew'):
      if row_y>clip.top-sub_h and row_y<clip.bottom:
       screen.blit(tiny.render(self.fit_text(tiny,'    crew / weapon',label_w),True,MUTED),(x,row_y))
       m=pygame.Rect(ctrl_x,row_y-2,btn,btn); a=pygame.Rect(right-btn,row_y-2,btn,btn)
       pygame.draw.rect(screen,(67,75,81),m,border_radius=4); pygame.draw.rect(screen,(67,75,81),a,border_radius=4)
       screen.blit(tiny.render('-',True,TEXT),(m.centerx-tiny.size('-')[0]//2,m.centery-tiny.size('-')[1]//2)); screen.blit(tiny.render('+',True,TEXT),(a.centerx-tiny.size('+')[0]//2,a.centery-tiny.size('+')[1]//2))
       val=str(w['operators_per_system']); screen.blit(tiny.render(val,True,(255,210,70)),((m.right+a.left-tiny.size(val)[0])//2,row_y))
       self.comp_buttons += [(m,('wops',ri,wi,-1)),(a,('wops',ri,wi,1))]
      row_y+=sub_h
    row_y+=4
   screen.set_clip(old)

   gap=10; save_w=max(130,int((content_w-gap)*0.57)); cancel_w=content_w-gap-save_w
   save=pygame.Rect(x,action_y,save_w,action_h); cancel=pygame.Rect(save.right+gap,action_y,cancel_w,action_h)
   pygame.draw.rect(screen,(64,105,76),save,border_radius=5); pygame.draw.rect(screen,(104,70,70),cancel,border_radius=5)
   st=self.fit_text(tiny,'SAVE CHANGES',save.w-12); ct=self.fit_text(tiny,'CANCEL',cancel.w-12)
   screen.blit(tiny.render(st,True,TEXT),(save.centerx-tiny.size(st)[0]//2,save.centery-tiny.size(st)[1]//2))
   screen.blit(tiny.render(ct,True,TEXT),(cancel.centerx-tiny.size(ct)[0]//2,cancel.centery-tiny.size(ct)[1]//2))
   self.comp_buttons += [(save,('save',)),(cancel,('cancel',))]
  elif self.selected:
   screen.blit(tiny.render(self.fit_text(tiny,'Selected: '+self.selected[1].get('id',''),content_w),True,(255,210,70)),(x,y)); y+=line_h
   screen.blit(tiny.render('Terrain object selected',True,MUTED),(x,y))
  else:
   # Visible data-driven unit selector.  Q/W remains as a keyboard shortcut, but users
   # should not have to discover hidden templates by cycling through names.
   screen.blit(tiny.render('AVAILABLE UNIT TEMPLATES',True,(255,210,70)),(x,y)); y+=line_h+3
   choices=self.compatible_types(); max_selector_y=status_top-90
   row_h=max(27,line_h+7)
   for idx,name in enumerate(choices):
    if y+row_h>max_selector_y: break
    td=self.type_defs.get(name,{})
    branch=str(td.get('branch','')).replace('_',' ').title()
    active=(idx==(self.type_idx%max(1,len(choices))))
    rect=pygame.Rect(x,y,content_w,row_h-3)
    pygame.draw.rect(screen,(68,82,92) if active else (47,55,61),rect,border_radius=4)
    if active: pygame.draw.rect(screen,(255,210,70),rect,2,border_radius=4)
    label=self.fit_text(tiny,f'{name}  —  {branch}',content_w-14)
    screen.blit(tiny.render(label,True,TEXT),(x+7,y+3))
    self.template_buttons.append((rect,idx)); y+=row_h
   if len(choices)>len(self.template_buttons):
    screen.blit(tiny.render(self.fit_text(tiny,'More templates: Q / W',content_w),True,MUTED),(x,y)); y+=line_h
   y+=3
   tips=['Click a template, then click the map to place it.',
         'Select a placed unit to edit its composition.',
         'Selected unit: Ctrl+Left/Right rotates watch direction; Shift+Arrows moves it.',
         'Area: click near first point to close.']
   max_tip_y=status_top-8
   for text in tips:
    for ln in self.wrap_text(tiny,text,content_w):
     if y+line_h>max_tip_y: break
     screen.blit(tiny.render(ln,True,MUTED),(x,y)); y+=line_h

  # Status remains readable at every window height instead of being hard-coded to y=870.
  pygame.draw.line(screen,(58,66,71),(x,status_top-4),(right,status_top-4),1)
  sy=status_top+2
  for ln in status_lines:
   screen.blit(tiny.render(self.fit_text(tiny,ln,content_w),True,MUTED),(x,sy)); sy+=line_h
 def run(self):
  pygame.init(); screen=pygame.display.set_mode((W,H),pygame.RESIZABLE); pygame.display.set_caption('MNS Map & Scenario Editor')
  self.update_layout(screen.get_size()); font,body,tiny=self.make_fonts(); clock=pygame.time.Clock(); running=True
  while running:
   for e in pygame.event.get():
    if e.type==pygame.QUIT:running=False
    elif e.type==pygame.VIDEORESIZE:
     size=(max(MIN_W,e.w),max(MIN_H,e.h))
     screen=pygame.display.set_mode(size,pygame.RESIZABLE)
     self.update_layout(size); font,body,tiny=self.make_fonts()
     self.message=f'Window resized: {size[0]} × {size[1]}'
    elif e.type==pygame.KEYDOWN:
     if pygame.K_1<=e.key<=pygame.K_9:self.tool=TOOLS[e.key-pygame.K_1];self.points=[]
     elif e.key==pygame.K_l:self.tool='LAKE';self.points=[]
     elif e.key==pygame.K_k:self.tool='BUILDING';self.points=[]
     elif e.key==pygame.K_v:self.tool='ELEVATION';self.points=[]
     elif e.key==pygame.K_h:self.tool='BARRICADE';self.points=[]
     elif e.key==pygame.K_COMMA:
      if self.tool=='BARRICADE':self.barricade_heading_deg=(self.barricade_heading_deg-10.0)%360.0
      else:self.elevation_m=max(-200.0,self.elevation_m-10.0)
     elif e.key==pygame.K_PERIOD:
      if self.tool=='BARRICADE':self.barricade_heading_deg=(self.barricade_heading_deg+10.0)%360.0
      else:self.elevation_m=min(3000.0,self.elevation_m+10.0)
     elif e.key==pygame.K_SEMICOLON:self.building_height_m=max(3.0,self.building_height_m-1.0)
     elif e.key==pygame.K_QUOTE:self.building_height_m=min(100.0,self.building_height_m+1.0)
     elif e.key==pygame.K_b:self.side='BLUE'
     elif e.key==pygame.K_r:self.side='RED'
     elif e.key==pygame.K_e:self.cycle_echelon()
     elif e.key==pygame.K_g:self.toggle_optional_attachment()
     elif e.key==pygame.K_q and self.compatible_types():self.type_idx=(self.type_idx-1)%len(self.compatible_types())
     elif e.key==pygame.K_w and self.compatible_types():self.type_idx=(self.type_idx+1)%len(self.compatible_types())
     elif e.key in (pygame.K_PLUS,pygame.K_EQUALS):self.river_width=min(500,self.river_width+10)
     elif e.key==pygame.K_MINUS:self.river_width=max(20,self.river_width-10)
     elif e.key==pygame.K_LEFTBRACKET:self.bridge_width=max(10,self.bridge_width-10)
     elif e.key==pygame.K_RIGHTBRACKET:self.bridge_width=min(300,self.bridge_width+10)
     elif e.key==pygame.K_DELETE:self.delete_selected()
     elif e.key==pygame.K_z and (e.mod & pygame.KMOD_CTRL):self.undo_delete()
     elif e.key==pygame.K_ESCAPE:self.selected=None; self.comp_draft=None; self.points=[]; self.message='Selection/drawing cleared'
     elif e.key==pygame.K_n:
      self.new_blank()
     elif e.key==pygame.K_m:
      self.set_map_size_dialog()
     elif e.key==pygame.K_s:
      if e.mod & pygame.KMOD_SHIFT:
       chosen=self.choose(True)
       if chosen:
        self.scenario_path=Path(chosen); self.terrain_path=self.scenario_path.with_name(self.scenario_path.stem+'_terrain.json'); self.save()
      else:self.save()
     elif e.key==pygame.K_o:
      p=self.choose(False)
      if p:self.load(Path(p))
     elif e.key in (pygame.K_LEFT,pygame.K_RIGHT,pygame.K_UP,pygame.K_DOWN):
      if (e.mod & pygame.KMOD_CTRL) and self.selected and self.selected[0]=='unit' and e.key in (pygame.K_LEFT,pygame.K_RIGHT):
       u=self.selected[1]; cur=float(u.get('watch_heading_deg',u.get('heading_deg',0.0)))
       step=-10.0 if e.key==pygame.K_LEFT else 10.0
       u['watch_heading_deg']=(cur+step)%360.0
       self.message=f"{u.get('id','unit')} watch direction: {u['watch_heading_deg']:.0f}° (Ctrl+Left/Right ±10°)"
      elif (e.mod & pygame.KMOD_SHIFT) and self.selected and self.selected[0]=='unit':
       u=self.selected[1]; step_m=max(5.0,25.0/self.zoom)
       dx=(-step_m if e.key==pygame.K_LEFT else step_m if e.key==pygame.K_RIGHT else 0)
       dy=(-step_m if e.key==pygame.K_UP else step_m if e.key==pygame.K_DOWN else 0)
       u['pos'][0]=max(0.0,min(self.world_size()[0],u['pos'][0]+dx))
       u['pos'][1]=max(0.0,min(self.world_size()[1],u['pos'][1]+dy))
      else:
       pan_px=80
       dx=(-pan_px if e.key==pygame.K_LEFT else pan_px if e.key==pygame.K_RIGHT else 0)
       dy=(-pan_px if e.key==pygame.K_UP else pan_px if e.key==pygame.K_DOWN else 0)
       self.pan(dx,dy)
    elif e.type==pygame.MOUSEWHEEL:
     pos=pygame.mouse.get_pos()
     if MAP.collidepoint(pos): self.zoom_at(pos,e.y)
     elif PANEL.collidepoint(pos) and self.comp_draft: self.comp_scroll=max(0,self.comp_scroll-e.y*3)
    elif e.type==pygame.MOUSEBUTTONDOWN and PANEL.collidepoint(e.pos):
     self.handle_panel_click(e.pos)
    elif e.type==pygame.MOUSEBUTTONDOWN and MAP.collidepoint(e.pos):
     p=self.clamp_world_point(self.s2w(e.pos))
     if e.button==1:
      if self.tool=='SELECT':self.select_object(self.nearest(p,e.pos))
      elif self.tool=='UNIT':self.add_unit(p)
      elif self.tool=='BARRICADE':self.add_barricade(p)
      elif self.should_close_polygon(e.pos):
       self.finish_shape(); self.message=f'{self.tool} polygon closed'
      else:self.points.append([round(p[0],1),round(p[1],1)])
     elif e.button==3:self.finish_shape()
   self.draw(screen,font,tiny); pygame.display.flip(); clock.tick(60)
  pygame.quit()

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('scenario',nargs='?',default=None,help='optional existing scenario; omitted = blank plain'); args=ap.parse_args(); Editor(args.scenario).run()
if __name__=='__main__':main()
