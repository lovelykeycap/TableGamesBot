from __future__ import annotations

import asyncio
import contextlib

from app.services.delivery_service import DeliveryService
from app.services.hat_service import HatService


class HatScheduler:
    """Database-backed deadlines and the pair-only visual turn timer."""

    def __init__(self, hats: HatService, delivery: DeliveryService) -> None:
        self._hats = hats
        self._delivery = delivery
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            for room_id in await self._hats.due_room_ids():
                if await self._hats.process_due(room_id):
                    await self._delivery.render_room(room_id)
            for room_id in await self._hats.active_turn_room_ids():
                for user_id in await self._hats.turn_user_ids(room_id):
                    await self._delivery.render_room_for_user(room_id, user_id)
            await asyncio.sleep(0.5)
