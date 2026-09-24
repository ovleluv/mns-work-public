"""Engagement subsystem of :class:`mnsim.simulation.Simulation` (mixin).

Perception-driven local N:M engagement grouping, target selection, infrastructure strikes and
the per-step combat pass.  Split out of simulation.py without changing behaviour; methods
operate on the Simulation instance (``self``).
"""
from __future__ import annotations
import math
from .model import Unit, UnitState


class EngagementMixin:
    # ---------- local N:M engagement, now perception-driven ----------
    def _direct_units(self): return [u for u in self.units.values() if u.alive and not u.metadata.get("noncombat_proxy")]

    @staticmethod
    def _has_direct_weapon(unit):
        return any(w.capability.upper() != "INDIRECT_FIRE" for _,w in unit.operational_weapons())

    @staticmethod
    def _has_indirect_weapon(unit):
        return any(w.capability.upper() == "INDIRECT_FIRE" for e in unit.elements.values() for w in e.weapons)

    def _indirect_candidates(self, shooter, mode):
        candidates=[]
        for uid in shooter.local_tracks:
            target=self.units.get(uid)
            if target is None or target.side==shooter.side or target.metadata.get("noncombat_proxy"):
                continue
            if mode=="COUNTER_BATTERY" and shooter.local_tracks[uid].classification.upper()!="ARTILLERY":
                continue
            if self._unit_can_affect(shooter,target,mode):
                candidates.append(target)
        return candidates

    def _build_engagements(self):
        units=self._direct_units(); link=float(self.combat_config["engagement_link_m"]); adj={u.uid:set() for u in units}
        has_direct={u.uid:self._has_direct_weapon(u) for u in units}
        # Hostile edges require at least one side to possess an actionable contact; no omniscient proximity trigger.
        for i,a in enumerate(units):
            for b in units[i+1:]:
                if a.side==b.side: continue
                ta=self._track_for(a,b); tb=self._track_for(b,a)
                # A local engagement exists whenever at least one side has a usable track and
                # either side can actually affect the other from its perceived weapon envelope.
                # This avoids the old fixed-420 m cutoff prematurely breaking tank/direct-fire fights.
                can_a = ta is not None and self._unit_can_affect(a,b)
                can_b = tb is not None and self._unit_can_affect(b,a)
                # Keep a short contact-link fallback for close encounters where classification/capability
                # prevents fire (e.g. rifle infantry facing armor after its AT specialist is lost).
                close_contact=(ta is not None or tb is not None) and a.distance_to(b)<=link and (has_direct[a.uid] or has_direct[b.uid])
                if can_a or can_b or close_contact:
                    adj[a.uid].add(b.uid); adj[b.uid].add(a.uid)
        # Nearby friendlies may join the same local engagement component, but still fire only on their own tracks.
        for a in units:
            if not adj[a.uid]: continue
            for b in units:
                if a.uid!=b.uid and a.side==b.side and a.distance_to(b)<=link*0.75:
                    adj[a.uid].add(b.uid); adj[b.uid].add(a.uid)
        seen=set(); groups=[]; by_id={u.uid:u for u in units}
        for u in units:
            if u.uid in seen or not adj[u.uid]: continue
            stack=[u.uid]; ids=[]
            while stack:
                cur=stack.pop()
                if cur in seen: continue
                # Sorted expansion: set iteration order depends on PYTHONHASHSEED, and the member
                # order decides who fires first and therefore the RNG draw sequence.
                seen.add(cur); ids.append(cur); stack.extend(sorted(adj[cur]-seen))
            members=[by_id[x] for x in ids]
            if len({m.side for m in members})>=2: groups.append(members)
        return groups

    def _unit_can_affect(self,shooter:Unit,target:Unit,mode="DIRECT"):
        return self.combat.unit_can_affect(shooter,target,mode)

    def _select_target(self,shooter,enemies,mode="DIRECT"):
        return self.combat.select_target(shooter,enemies,mode)

    def _infrastructure_target_alive(self, target_id: str) -> bool:
        if not self.terrain:
            return False
        br=self.terrain.bridge_by_id(target_id)
        bld=self.terrain.building_by_id(target_id)
        return ((br is not None and self.terrain.bridge_operational(br))
                or (bld is not None and self.terrain.building_operational(bld)))

    def _advance_infrastructure_index(self, u: Unit, targets, log_completed: bool) -> int:
        """Skip already-destroyed targets; return the index of the next live target."""
        idx=int(u.metadata.get("infrastructure_strike_index",0))
        while idx<len(targets) and not self._infrastructure_target_alive(str(targets[idx])):
            if log_completed:
                self.log("INFRASTRUCTURE_TARGET_COMPLETE",unit=u.uid,target=str(targets[idx]))
            idx+=1; u.metadata["infrastructure_strike_index"]=idx
        return idx

    def _execute_infrastructure_strike(self, arty:Unit) -> bool:
        o=arty.current_order
        if not o or o.kind!="STRIKE_INFRASTRUCTURE": return False
        # A scheduled strike cannot fire before its start time, nor while orders are suspended
        # for crew loss; the battery simply holds (and skips autonomous fire) meanwhile.
        if (o.start_at_s is not None and self.time<o.start_at_s) or arty.state==UnitState.COMBAT_INEFFECTIVE:
            return True
        targets=list(o.params.get("targets",[]))
        idx=self._advance_infrastructure_index(arty,targets,log_completed=True)
        if idx>=len(targets):return True
        bid=str(targets[idx]);br=self.terrain.bridge_by_id(bid);bld=self.terrain.building_by_id(bid) if hasattr(self.terrain,"building_by_id") else None
        aim=tuple(self.terrain.bridge_center(br)) if br is not None else tuple(self.terrain.building_center(bld));requested=False
        for el,w in arty.operational_weapons():
            if w.capability.upper()!="INDIRECT_FIRE": continue
            if math.dist(arty.pos,aim)>w.range_m: continue
            requested=self.fire_control.request_coordinate(arty,el,w,aim,f"INFRA:{bid}","INFRASTRUCTURE_STRIKE",float(o.params.get("coordinate_error_m",3.0))) or requested
        if requested:
            self.log("INFRASTRUCTURE_STRIKE_REQUEST",unit=arty.uid,target=bid,aim=[round(aim[0],1),round(aim[1],1)])
        return True

    def _combat_step(self):
        for u in self.units.values():
            if u.alive:
                u.mark_unavailable_fire_cycles()
        groups=self._build_engagements(); self.engagements=[]; engaged_ids=set()
        for idx,members in enumerate(groups,1):
            blue=[u for u in members if u.side.value=="BLUE" and u.alive]; red=[u for u in members if u.side.value=="RED" and u.alive]
            if not blue or not red:continue
            engaged_ids.update(u.uid for u in members); cx=sum(u.pos[0] for u in members)/len(members); cy=sum(u.pos[1] for u in members)/len(members)
            self.engagements.append({"id":f"E{idx}","center":(cx,cy),"members":[u.uid for u in members],"blue":len(blue),"red":len(red)})
            # Formation target_id remains a primary observation/mission cue, while actual direct
            # fire is allocated independently by FormationElement/weapon stream.  Shared allocation
            # counters add diminishing returns so multiple friendly formations do not all pile every
            # eligible stream onto the same enemy formation when alternatives are available.
            blue_alloc={}; red_alloc={}
            passes=[(blue,red,blue_alloc),(red,blue,red_alloc)]
            # Alternate which side resolves first each step so neither side systematically gets
            # the first RNG draws or the first immediate side effects (cues, structure damage).
            self._combat_step_index=getattr(self,"_combat_step_index",0)+1
            if self._combat_step_index%2==0:
                passes.reverse()
            for shooters,enemies,alloc in passes:
                for s in shooters:
                    if not self._has_direct_weapon(s):continue
                    t=self._select_target(s,enemies)
                    if t:
                        if s.current_order and s.current_order.kind=="ATTACK": s.state=UnitState.ENGAGING
                        self.combat.fire_hybrid(s,enemies,t,alloc)

        # Artillery targeting is perception/doctrine driven. Enemy artillery is treated as a
        # high-payoff counterfire target whenever a credible ARTILLERY track exists, regardless
        # of whether it came from radar, a local observer, or C2 sharing. A radar point-of-origin
        # solution is retained long enough to support follow-on salvos after FDC/reload delays.
        # Any formation with an operational INDIRECT_FIRE weapon participates (mortar sections in
        # infantry units included); the branch label does not decide capability.
        for arty in [u for u in self.units.values() if u.alive and self._has_indirect_weapon(u)
                     and "_scoot_dest" not in u.metadata]:
            # Explicit infrastructure strike orders supersede autonomous counterfire/fire support.
            if self._execute_infrastructure_strike(arty):
                continue
            counterfire=self._indirect_candidates(arty,"COUNTER_BATTERY")
            if counterfire:
                target=self._select_target(arty,counterfire,"COUNTER_BATTERY")
                if target:
                    for element,weapon in arty.operational_weapons():
                        if weapon.capability.upper()=="INDIRECT_FIRE":
                            self.fire_control.request(arty,target,element,weapon,"COUNTER_BATTERY")
                if bool(self.targeting_doctrine.get("counter_battery",{}).get("prefer_over_other_fire_support",True)):
                    continue
            arty.metadata['_fire_support_engaged_ids']=sorted(engaged_ids)
            cand=self._indirect_candidates(arty,"FIRE_SUPPORT")
            if cand:
                target=self._select_target(arty,cand,"FIRE_SUPPORT")
                if target:
                    for element,weapon in arty.operational_weapons():
                        if weapon.capability.upper()=="INDIRECT_FIRE":
                            self.fire_control.request(arty,target,element,weapon,"FIRE_SUPPORT")
