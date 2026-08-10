from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.services.delivery_service import DeliveryService
from app.services.hat_service import HatService
from app.services.locks import RoomLocks
from app.services.room_service import RoomService
from app.storage.database import Database
from app.storage.models import Base, CatalogLocation, CatalogTheme


class FakeBot:
    def __init__(self) -> None:
        self.next_message_id = 1
        self.sent: list[int] = []
        self.edited: list[int] = []
        self.deleted: list[int] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: object) -> SimpleNamespace:
        message_id = self.next_message_id
        self.next_message_id += 1
        self.sent.append(message_id)
        return SimpleNamespace(message_id=message_id)

    async def edit_message_text(
        self, *, chat_id: int, message_id: int, text: str, **kwargs: object
    ) -> None:
        self.edited.append(message_id)

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        self.deleted.append(message_id)


@pytest.mark.asyncio
async def test_panels_are_reused_within_a_context_and_replaced_between_contexts(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/delivery.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    rooms = RoomService(database, RoomLocks())
    for user_id in range(1, 5):
        await rooms.ensure_user(user_id, f"Игрок {user_id}", None)
    async with database.session() as session, session.begin():
        theme = CatalogTheme(name="Учёба", is_active=True)
        session.add(theme)
        await session.flush()
        session.add(CatalogLocation(theme_id=theme.id, name="Школа", is_active=True))

    bot = FakeBot()
    delivery = DeliveryService(bot, database, rooms, "testbot")  # type: ignore[arg-type]
    try:
        await delivery.render_home(1)
        await delivery.render_home_notice(1, "Проверка")
        assert bot.sent == [1]
        assert bot.edited == [1]

        room_id = await rooms.create_spy_room(1)
        await delivery.render_room_for_user(room_id, 1)
        await rooms.set_join_open(room_id, 1, False)
        await delivery.render_room_for_user(room_id, 1)
        await rooms.set_join_open(room_id, 1, True)
        await delivery.render_room_for_user(room_id, 1)
        assert bot.sent == [1, 2]
        assert bot.deleted == [1]
        assert bot.edited == [1, 2, 2]

        join_code = (await rooms.get_room_view(room_id, 1)).join_code
        for user_id in range(2, 5):
            await rooms.join_by_code(join_code, user_id)
        await rooms.set_exact_spy_count(room_id, 1, 1)
        await rooms.start_spy_game(room_id, 1)
        await delivery.render_room_for_user(room_id, 1)
        await rooms.emergency_cancel(room_id, 1)
        await delivery.render_room_for_user(room_id, 1)
        assert bot.sent == [1, 2, 3]
        assert bot.deleted == [1, 2]
        assert bot.edited == [1, 2, 2, 3]

        builder = InlineKeyboardBuilder()
        builder.button(text="Админка", callback_data="a:admin")
        await delivery.render_custom_home(1, "<b>Админка</b>", builder.as_markup())
        await delivery.render_home(1)
        assert bot.sent == [1, 2, 3, 4, 5]
        assert bot.deleted == [1, 2, 3, 4]

        await delivery.announce_room_notice(room_id, "Временное сообщение", ttl_seconds=0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert bot.sent == [1, 2, 3, 4, 5, 6, 7, 8, 9]
        assert bot.deleted == [1, 2, 3, 4, 6, 7, 8, 9]
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_hat_word_prompt_is_not_replaced_by_another_players_progress_update(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/hat-delivery.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    rooms = RoomService(database, RoomLocks())
    for user_id in range(1, 4):
        await rooms.ensure_user(user_id, f"Игрок {user_id}", None)
    hats = HatService(rooms)
    bot = FakeBot()
    delivery = DeliveryService(bot, database, rooms, "testbot")  # type: ignore[arg-type]
    try:
        room_id = await hats.create_room(1)
        code = (await hats.get_room_view(room_id, 1)).join_code
        for user_id in (2, 3):
            await rooms.join_by_code(code, user_id)
        await hats.randomize_seats(room_id, 1)
        await hats.start_collection(room_id, 1)

        await delivery.render_room_for_user(room_id, 1)
        await delivery.render_hat_word_prompt(room_id, 1)
        prompt_message_id = bot.sent[-1]
        edits_before_progress_update = bot.edited.count(prompt_message_id)

        await hats.add_word(room_id, 2, "Боб")
        await delivery.render_room(room_id)

        assert bot.edited.count(prompt_message_id) == edits_before_progress_update
        await delivery.release_hat_word_prompt(room_id, 1)
        await delivery.render_room_for_user(room_id, 1)
        assert bot.edited.count(prompt_message_id) == edits_before_progress_update + 1
    finally:
        await database.dispose()
