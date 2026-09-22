
from __future__ import annotations
import math
from .model import Track


class ContactBeliefPolicy:
    """Persistent enemy-order-of-battle belief above short-lived firing tracks.

    A stale/lost firing solution should not mean "the enemy ceased to exist".  This policy keeps
    a non-actionable inferred contact for situational awareness until evidence supports removal.
    It is intentionally configurable so later doctrine can implement more/less conservative memory.

    Important separation:
      - Track.actionable(): may I shoot/maneuver on this information now?
      - belief_confidence/existence_confirmed: do I still assess that this enemy formation exists?

    Inferred contacts are UI/intelligence beliefs only; they do not by themselves authorize fire.
    """

    def __init__(self, sim):
        self.sim=sim

    def on_observation(self, tr: Track, classification: str | None = None):
        tr.existence_confirmed=True
        tr.last_confirmed_time=self.sim.time
        tr.belief_confidence=max(tr.belief_confidence, tr.confidence, 0.55)
        if classification and classification != "UNKNOWN":
            tr.classification=classification

    def age(self, tr: Track):
        if not tr.existence_confirmed:
            return
        cfg=self.sim.combat_config
        grace=float(cfg.get("belief_grace_s",90.0))
        half=float(cfg.get("belief_half_life_s",900.0))
        floor=float(cfg.get("belief_floor_confidence",0.28))
        age=max(0.0,self.sim.time-tr.last_confirmed_time)
        if age <= grace:
            return
        # Exponential decay toward a nonzero floor: absence of observation is not destruction evidence.
        start=max(tr.belief_confidence,floor)
        decay=0.5 ** ((age-grace)/max(1.0,half))
        tr.belief_confidence=max(floor,start*decay)

    def inferred_visible(self, tr: Track) -> bool:
        if not tr.existence_confirmed:
            return False
        return tr.belief_confidence >= float(self.sim.combat_config.get("belief_display_threshold",0.25))

    def mark_destroyed(self, observer_track: Track):
        # Explicit terminal evidence path. Future BDA can call this only when confidence criteria are met.
        observer_track.existence_confirmed=False
        observer_track.belief_confidence=0.0
        observer_track.state="LOST"
