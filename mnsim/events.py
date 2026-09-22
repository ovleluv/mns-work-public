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

    def __len__(self):
        return len(self._q)
