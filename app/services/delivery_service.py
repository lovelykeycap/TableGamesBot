from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select

from app.bot.renderers import RoomRenderer, home_markup, home_text
from app.domain.errors import DomainError
from app.services.room_service import RoomService
from app.storage.database import Database
from app.storage.models import HomePanel, RoomPanel


class DeliveryService:
    """Keeps one editable bot message per room and participant."""

    def __init__(self, bot: Bot, database: Database, rooms: RoomService, bot_username: str) -> None:
        self._bot = bot
        self._database = database
        self._rooms = rooms
        self._renderer = RoomRenderer(bot_username)

    async def render_home(self, user_id: int) -> None:
        await self._render_home(user_id, home_text())

    async def render_home_notice(self, user_id: int, notice: str) -> None:
        await self._render_home(user_id, f"<b>Настольные игры</b>\n{notice}")

    async def render_custom_home(
        self, user_id: int, text: str, markup: InlineKeyboardMarkup
    ) -> None:
        await self._render_home(user_id, text, markup)

    async def _render_home(
        self, user_id: int, text: str, markup: InlineKeyboardMarkup | None = None
    ) -> None:
        markup = markup or home_markup()
        panel = await self._home_panel(user_id)
        try:
            if panel is None:
                message = await self._bot.send_message(user_id, text, reply_markup=markup)
                await self._save_home_panel(user_id, message.message_id)
            else:
                await self._bot.edit_message_text(
                    chat_id=user_id,
                    message_id=panel.message_id,
                    text=text,
                    reply_markup=markup,
                )
        except TelegramBadRequest as error:
            if "message is not modified" in str(error).lower():
                return
            message = await self._bot.send_message(user_id, text, reply_markup=markup)
            await self._save_home_panel(user_id, message.message_id)
        except TelegramForbiddenError:
            return

    async def render_room(self, room_id: int, *, retry_after_failure: bool = True) -> None:
        failed = False
        for user_id in await self._rooms.room_recipients(room_id):
            if await self.render_room_for_user(room_id, user_id):
                failed = True
        if failed and retry_after_failure:
            await self.render_room(room_id, retry_after_failure=False)

    async def render_room_for_user(self, room_id: int, user_id: int) -> bool:
        """Render and return True when Telegram proved this user unreachable."""
        try:
            view = await self._rooms.get_room_view(room_id, user_id)
            text, markup = self._renderer.render(view)
            panel = await self._room_panel(room_id, user_id)
            if panel is None:
                message = await self._bot.send_message(user_id, text, reply_markup=markup)
                await self._save_room_panel(room_id, user_id, message.message_id)
            else:
                await self._bot.edit_message_text(
                    chat_id=user_id,
                    message_id=panel.message_id,
                    text=text,
                    reply_markup=markup,
                )
            return False
        except TelegramBadRequest as error:
            if "message is not modified" in str(error).lower():
                return False
            try:
                message = await self._bot.send_message(user_id, text, reply_markup=markup)
                await self._save_room_panel(room_id, user_id, message.message_id)
                return False
            except TelegramForbiddenError:
                await self._rooms.mark_delivery_failure(room_id, user_id)
                return True
        except TelegramForbiddenError:
            await self._rooms.mark_delivery_failure(room_id, user_id)
            return True
        except DomainError:
            return False

    async def render_room_variant(
        self, room_id: int, user_id: int, text: str, markup: InlineKeyboardMarkup
    ) -> None:
        panel = await self._room_panel(room_id, user_id)
        if panel is None:
            await self.render_room_for_user(room_id, user_id)
            return
        try:
            await self._bot.edit_message_text(
                chat_id=user_id, message_id=panel.message_id, text=text, reply_markup=markup
            )
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).lower():
                await self.render_room_for_user(room_id, user_id)
        except TelegramForbiddenError:
            await self._rooms.mark_delivery_failure(room_id, user_id)

    async def render_theme_selector(self, room_id: int, user_id: int, page: int) -> None:
        view = await self._rooms.get_room_view(room_id, user_id)
        text, markup = self._renderer.render_theme_selector(view, page)
        await self.render_room_variant(room_id, user_id, text, markup)

    async def render_candidate_selector(self, room_id: int, user_id: int, page: int) -> None:
        view = await self._rooms.get_room_view(room_id, user_id)
        text, markup = self._renderer.render_candidate_selector(view, page)
        await self.render_room_variant(room_id, user_id, text, markup)

    async def render_kick_selector(self, room_id: int, user_id: int, page: int) -> None:
        view = await self._rooms.get_room_view(room_id, user_id)
        text, markup = self._renderer.render_kick_selector(view, page)
        await self.render_room_variant(room_id, user_id, text, markup)

    async def _room_panel(self, room_id: int, user_id: int) -> RoomPanel | None:
        async with self._database.session() as session:
            return await session.scalar(
                select(RoomPanel).where(RoomPanel.room_id == room_id, RoomPanel.user_id == user_id)
            )

    async def _home_panel(self, user_id: int) -> HomePanel | None:
        async with self._database.session() as session:
            return await session.get(HomePanel, user_id)

    async def _save_room_panel(self, room_id: int, user_id: int, message_id: int) -> None:
        async with self._database.session() as session, session.begin():
            panel = await session.scalar(
                select(RoomPanel).where(RoomPanel.room_id == room_id, RoomPanel.user_id == user_id)
            )
            if panel is None:
                session.add(RoomPanel(room_id=room_id, user_id=user_id, message_id=message_id))
            else:
                panel.message_id = message_id

    async def _save_home_panel(self, user_id: int, message_id: int) -> None:
        async with self._database.session() as session, session.begin():
            panel = await session.get(HomePanel, user_id)
            if panel is None:
                session.add(HomePanel(user_id=user_id, message_id=message_id))
            else:
                panel.message_id = message_id
