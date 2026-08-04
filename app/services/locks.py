from __future__ import annotations

import asyncio
from collections import defaultdict


class RoomLocks:
    """Single-process serialization for room state transitions.

    The v1 deployment intentionally has one bot process. These locks prevent two
    simultaneous callback requests from evaluating the same vote independently.
    """

    def __init__(self) -> None:
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def for_room(self, room_id: int) -> asyncio.Lock:
        return self._locks[room_id]
