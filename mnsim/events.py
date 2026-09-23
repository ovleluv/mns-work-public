from __future__ import annotations
from dataclasses import dataclass, field
from heapq import heappush, heappop
from typing import Any, Dict, List
import itertools

@dataclass(order=True)
class SimEvent:
    time: float
    seq: int
    kind: str = field(compare=False)
    payload: Dict[str, Any] = field(default_factory=dict, compare=False)

class EventQueue:
    def __init__(self):
        self._q: List[SimEvent] = []
        self._seq = itertools.count()

    def push(self, time: float, kind: str, **payload):
        heappush(self._q, SimEvent(time, next(self._seq), kind, payload))

    def pop_due(self, now: float):
        while self._q and self._q[0].time <= now:
            yield heappop(self._q)

    def remap_formation_damage(self, source_uid: str, destination_uid: str,
                               element_ids: Dict[str, str]):
        """Follow element ownership when formations aggregate or deaggregate.

        Direct and area effects already selected a physical element. Moving that element to a
        different active unit must not make an in-flight effect disappear. Event ordering and
        impact times are unchanged.
        """
        for event in self._q:
            if (event.kind not in ("ELEMENT_LOSS", "EQUIPMENT_EFFECT")
                    or event.payload.get("target") != source_uid):
                continue
            mapped = element_ids.get(event.payload.get("element"))
            if mapped is not None:
                event.payload["target"] = destination_uid
                event.payload["element"] = mapped

    def remap_equipment_effects(self, source_uid: str, element_id: str,
                                removed_index: int, detached_uid: str):
        """Keep queued spatial hits attached to their physical item after a split.

        Only payloads change; event times and heap ordering remain untouched.
        """
        for event in self._q:
            if (event.kind != "EQUIPMENT_EFFECT" or event.payload.get("target") != source_uid
                    or event.payload.get("element") != element_id):
                continue
            index=event.payload.get("item_index")
            if index is None:
                continue  # Non-spatial direct fire chooses its item when the event resolves.
            if index == removed_index:
                event.payload["target"] = detached_uid
                event.payload["item_index"] = 0
            elif index > removed_index:
                event.payload["item_index"] = index - 1

    def __len__(self):
        return len(self._q)
