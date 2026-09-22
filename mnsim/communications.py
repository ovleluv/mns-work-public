from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional
import math


@dataclass
class CommLinkProfile:
    """Transport/link characteristics for the abstract tactical network.

    The current default profile is intentionally permissive.  Future radio/wire/jamming
    implementations should alter link availability, latency, reliability and error here rather
    than bypassing the communications layer from tactical code.
    """
    name: str = "TACTICAL_RADIO"
    medium: str = "RADIO"          # RADIO / WIRE / DATA_LINK / COURIER ...
    min_delay_s: float = 1.0
    max_delay_s: float = 4.0
    reliability: float = 0.995
    max_range_m: Optional[float] = None
    jam_resistance: float = 0.5


@dataclass
class CommMessage:
    message_type: str
    sender_uid: str
    recipient_uid: str
    created_t: float
    payload: Dict[str, Any] = field(default_factory=dict)
    channel: str = "TACTICAL_RADIO"
    priority: int = 10


class CommunicationNetwork:
    """Thin communications abstraction between tactical entities.

    Current routing mode is side-wide dissemination: any active friendly entity is potentially
    reachable regardless of echelon.  This is a deliberate temporary baseline, not instantaneous
    telepathy: every delivery is a recipient-specific event with latency/reliability.  Later work
    can replace ``eligible_recipients``/``link_profile`` with platoon-company-battalion routing,
    radio nets, wired links, relay nodes, EMCON, LOS radio propagation and jamming without changing
    Track/Doctrine/BML consumers.
    """
    def __init__(self, sim):
        self.sim = sim

    @property
    def config(self) -> dict:
        return dict(self.sim.combat_config.get("communications", {}))

    def _profile(self, sender, receiver, message_type: str) -> CommLinkProfile:
        cfg=self.config
        p=dict(cfg.get("default_link", {}))
        # Per-unit overrides are useful later for different radios / wire-only units.
        p.update(dict(sender.metadata.get("communications", {}).get("tx_profile", {})))
        p.update(dict(receiver.metadata.get("communications", {}).get("rx_profile", {})))
        return CommLinkProfile(
            name=str(p.get("name","TACTICAL_RADIO")), medium=str(p.get("medium","RADIO")),
            min_delay_s=float(p.get("min_delay_s",1.0)), max_delay_s=float(p.get("max_delay_s",4.0)),
            reliability=float(p.get("reliability",0.995)),
            max_range_m=(None if p.get("max_range_m",None) is None else float(p["max_range_m"])),
            jam_resistance=float(p.get("jam_resistance",0.5)),
        )

    def _jamming_factor(self, sender, receiver, profile: CommLinkProfile) -> float:
        """Return 0..1 delivery multiplier. Neutral today; extension point for EW/jamming."""
        cfg=self.config
        # Global scalar is enough for experiments before spatial jammer entities are introduced.
        jam=float(cfg.get("global_jamming_strength",0.0))
        jam=max(0.0,min(1.0,jam))
        return max(0.0,1.0-jam*(1.0-max(0.0,min(1.0,profile.jam_resistance))))

    def eligible_recipients(self, sender, side=None) -> Iterable:
        side=side or sender.side
        mode=str(self.config.get("routing_mode","SIDE_WIDE")).upper()
        # SIDE_WIDE is the current baseline requested for cross-platoon/company SA.
        if mode == "SIDE_WIDE":
            return [u for u in self.sim.units.values() if u.can_communicate and u.side==side and u.uid!=sender.uid]
        # Safe fallback. Future hierarchy/net routing plugs in here.
        return [u for u in self.sim.units.values() if u.can_communicate and u.side==side and u.uid!=sender.uid]

    def send(self, sender_uid: str, recipient_uid: str, message_type: str, payload: dict,
             priority: int = 10) -> bool:
        sender=self.sim.units.get(sender_uid); recv=self.sim.units.get(recipient_uid)
        if sender is None or recv is None or not sender.can_communicate or not recv.can_communicate or sender.side!=recv.side:
            return False
        profile=self._profile(sender,recv,message_type)
        if profile.max_range_m is not None and math.dist(sender.pos,recv.pos)>profile.max_range_m:
            self.sim.log("COMM_DROP",source=sender_uid,recipient=recipient_uid,message_type=message_type,reason="OUT_OF_RANGE")
            return False
        p=max(0.0,min(1.0,profile.reliability*self._jamming_factor(sender,recv,profile)))
        if self.sim.rng.random()>p:
            self.sim.log("COMM_DROP",source=sender_uid,recipient=recipient_uid,message_type=message_type,reason="LINK_LOSS",channel=profile.name)
            return False
        lo=min(profile.min_delay_s,profile.max_delay_s); hi=max(profile.min_delay_s,profile.max_delay_s)
        delay=self.sim.rng.uniform(lo,hi)
        msg={"message_type":message_type,"sender_uid":sender_uid,"recipient_uid":recipient_uid,
             "created_t":self.sim.time,"payload":dict(payload),"channel":profile.name,"priority":priority}
        self.sim.events.push(self.sim.time+delay,"COMM_DELIVER",message=msg)
        self.sim.log("COMM_TX",source=sender_uid,recipient=recipient_uid,message_type=message_type,
                     channel=profile.name,delay_s=round(delay,2))
        return True

    def broadcast_side(self, sender_uid: str, message_type: str, payload: dict, priority: int = 10) -> int:
        sender=self.sim.units.get(sender_uid)
        if sender is None or not sender.can_communicate:
            return 0
        n=0
        for recv in self.eligible_recipients(sender):
            n += 1 if self.send(sender.uid,recv.uid,message_type,payload,priority) else 0
        return n
