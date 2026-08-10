from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy import delete, select

from app.bot.hat_renderer import HatRenderer
from app.bot.renderers import RoomRenderer, home_markup, home_text
from app.domain.errors import DomainError
from app.domain.games.hat import GAME_TYPE, PHASE_COLLECTING, PHASE_CONFIGURED
from app.services.hat_service import HatService
from app.services.room_service import RoomService
from app.storage.database import Database
from app.storage.models import HomePanel, RoomPanel

_PANEL_CONTEXT_INPUT = "input"


class DeliveryService:
    """Keeps one editable interface panel per active user context."""

    def __init__(
        self,
        bot: Bot,
        database: Database,
        rooms: RoomService,
        hats: HatService | str,
        bot_username: str | None = None,
    ) -> None:
        if isinstance(hats, str):
            bot_username = hats
            hats = HatService(rooms)
        if bot_username is None:
            raise ValueError("Bot username is required")
        self._bot = bot
        self._database = database
        self._rooms = rooms
        self._hats = hats
        self._renderer = RoomRenderer(bot_username)
        self._hat_renderer = HatRenderer(bot_username)
        self._temporary_cleanup_tasks: set[asyncio.Task[None]] = set()

    async def render_home(self, user_id: int) -> None:
        await self._render_home(user_id, home_text(), context="home")

    async def render_home_notice(self, user_id: int, notice: str) -> None:
        await self._render_home(user_id, f"<b>Настольные игры</b>\n{notice}", context="home")

    async def render_custom_home(
        self,
        user_id: int,
        text: str,
        markup: InlineKeyboardMarkup,
        *,
        context: str = "admin",
    ) -> None:
        await self._render_home(user_id, text, markup, context=context)

    async def _render_home(
        self,
        user_id: int,
        text: str,
        markup: InlineKeyboardMarkup | None = None,
        *,
        context: str,
    ) -> None:
        markup = markup or home_markup()
        panel = await self._home_panel(user_id)
        if (
            panel is not None
            and panel.context == context
            and not await self._has_room_panels(user_id)
        ):
            try:
                await self._bot.edit_message_text(
                    chat_id=user_id,
                    message_id=panel.message_id,
                    text=text,
                    reply_markup=markup,
                )
                return
            except TelegramBadRequest as error:
                if "message is not modified" in str(error).lower():
                    return
            except TelegramForbiddenError:
                return
        await self._retire_room_panels(user_id)
        await self._retire_home_panel(user_id)
        try:
            message = await self._bot.send_message(
                user_id,
                text,
                reply_markup=markup,
                disable_notification=True,
            )
            await self._save_home_panel(user_id, message.message_id, context)
        except TelegramBadRequest:
            return
        except TelegramForbiddenError:
            return

    async def render_room(self, room_id: int, *, retry_after_failure: bool = True) -> None:
        failed = False
        for user_id in await self._rooms.room_recipients(room_id):
            if await self.render_room_for_user(room_id, user_id):
                failed = True
        if failed and retry_after_failure:
            await self.render_room(room_id, retry_after_failure=False)

    async def announce_room_notice(
        self, room_id: int, text: str, *, ttl_seconds: float = 5
    ) -> None:
        """Send a short-lived room-wide event message below the game panel."""
        failed = False
        for user_id in await self._rooms.room_recipients(room_id):
            try:
                message = await self._bot.send_message(
                    user_id,
                    text,
                    disable_notification=True,
                )
            except TelegramBadRequest:
                continue
            except TelegramForbiddenError:
                await self._mark_delivery_failure(room_id, user_id)
                failed = True
                continue
            task = asyncio.create_task(
                self._delete_temporary_message(user_id, message.message_id, ttl_seconds)
            )
            self._temporary_cleanup_tasks.add(task)
            task.add_done_callback(self._temporary_cleanup_tasks.discard)
        if failed:
            await self.render_room(room_id)

    async def render_room_for_user(
        self, room_id: int, user_id: int, *, force: bool = False
    ) -> bool:
        """Edit the current context; replace it only when the context changes."""
        try:
            game_type = await self._rooms.room_game_type(room_id)
            if game_type == GAME_TYPE:
                view = await self._hats.get_room_view(room_id, user_id)
                text, markup = self._hat_renderer.render(view)
                context = "lobby" if view.phase == PHASE_CONFIGURED else "game"
            else:
                view = await self._rooms.get_room_view(room_id, user_id)
                text, markup = self._renderer.render(view)
                context = "lobby" if view.status == "lobby" else "game"
            panel = await self._room_panel(room_id, user_id)
            if (
                not force
                and game_type == GAME_TYPE
                and panel is not None
                and panel.context == _PANEL_CONTEXT_INPUT
                and view.phase == PHASE_COLLECTING
            ):
                return False
            if (
                panel is not None
                and panel.context == context
                and await self._home_panel(user_id) is None
                and not await self._has_other_room_panels(user_id, room_id)
            ):
                try:
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
                except TelegramForbiddenError:
                    await self._mark_delivery_failure(room_id, user_id)
                    return True
            await self._retire_home_panel(user_id)
            await self._retire_room_panels(user_id)
            message = await self._bot.send_message(
                user_id,
                text,
                reply_markup=markup,
                disable_notification=True,
            )
            await self._save_room_panel(room_id, user_id, message.message_id, context)
            return False
        except TelegramBadRequest:
            return False
        except TelegramForbiddenError:
            await self._mark_delivery_failure(room_id, user_id)
            return True
        except DomainError:
            return False

    async def render_room_variant(
        self,
        room_id: int,
        user_id: int,
        text: str,
        markup: InlineKeyboardMarkup,
        *,
        panel_context: str | None = None,
    ) -> None:
        panel = await self._room_panel(room_id, user_id)
        if panel is None:
            await self.render_room_for_user(room_id, user_id)
            return
        if panel_context is not None:
            await self._set_room_panel_context(room_id, user_id, panel_context)
        try:
            await self._bot.edit_message_text(
                chat_id=user_id, message_id=panel.message_id, text=text, reply_markup=markup
            )
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).lower():
                if panel_context is not None:
                    await self._set_room_panel_context(room_id, user_id, "game")
                await self.render_room_for_user(room_id, user_id, force=True)
        except TelegramForbiddenError:
            await self._mark_delivery_failure(room_id, user_id)

    async def render_hat_word_prompt(
        self,
        room_id: int,
        user_id: int,
        *,
        word_id: int | None = None,
        error: str | None = None,
    ) -> None:
        view = await self._hats.get_room_view(room_id, user_id)
        word = next((item for item in view.my_words if item.id == word_id), None)
        text, markup = self._hat_renderer.render_word_prompt(view, editing=word, error=error)
        await self.render_room_variant(
            room_id, user_id, text, markup, panel_context=_PANEL_CONTEXT_INPUT
        )

    async def release_hat_word_prompt(self, room_id: int, user_id: int) -> None:
        await self._set_room_panel_context(room_id, user_id, "game")

    async def render_hat_word_editor(self, room_id: int, user_id: int, word_id: int) -> None:
        view = await self._hats.get_room_view(room_id, user_id)
        word = next((item for item in view.my_words if item.id == word_id), None)
        if word is None:
            await self.render_room_for_user(room_id, user_id)
            return
        text, markup = self._hat_renderer.render_word_editor(view, word)
        await self.render_room_variant(room_id, user_id, text, markup)

    async def render_hat_word_list(self, room_id: int, user_id: int, page: int) -> None:
        view = await self._hats.get_room_view(room_id, user_id)
        text, markup = self._hat_renderer.render_word_list(view, page)
        await self.render_room_variant(room_id, user_id, text, markup)

    async def render_hat_top(self, room_id: int, user_id: int) -> None:
        view = await self._hats.get_room_view(room_id, user_id)
        text, markup = self._hat_renderer.render_top(view)
        await self.render_room_variant(room_id, user_id, text, markup)

    async def render_hat_manual_order(self, room_id: int, user_id: int) -> None:
        view = await self._hats.get_room_view(room_id, user_id)
        text, markup = self._hat_renderer.render_manual_order(view)
        await self.render_room_variant(room_id, user_id, text, markup)

    async def render_hat_random_order(self, room_id: int, user_id: int) -> None:
        view = await self._hats.get_room_view(room_id, user_id)
        text, markup = self._hat_renderer.render_random_order(view)
        await self.render_room_variant(room_id, user_id, text, markup)

    async def render_hat_appeal_selector(self, room_id: int, user_id: int, page: int) -> None:
        view = await self._hats.get_room_view(room_id, user_id)
        text, markup = self._hat_renderer.render_appeal_selector(view, page)
        await self.render_room_variant(room_id, user_id, text, markup)

    async def render_hat_violation_confirm(self, room_id: int, user_id: int) -> None:
        view = await self._hats.get_room_view(room_id, user_id)
        text, markup = self._hat_renderer.render_violation_confirm(view)
        await self.render_room_variant(room_id, user_id, text, markup)

    async def render_hat_cancel_confirm(self, room_id: int, user_id: int) -> None:
        view = await self._hats.get_room_view(room_id, user_id)
        text, markup = self._hat_renderer.render_cancel_confirm(view)
        await self.render_room_variant(room_id, user_id, text, markup)

    async def render_hat_cancel_phrase(
        self, room_id: int, user_id: int, *, error: str | None = None
    ) -> None:
        view = await self._hats.get_room_view(room_id, user_id)
        text, markup = self._hat_renderer.render_cancel_phrase(view, error)
        await self.render_room_variant(room_id, user_id, text, markup)

    async def delete_processed_message(self, message: Message) -> None:
        """Remove command and form-input messages from a private bot chat."""
        if message.chat.type != ChatType.PRIVATE:
            return
        try:
            await self._bot.delete_message(message.chat.id, message.message_id)
        except (TelegramBadRequest, TelegramForbiddenError):
            return

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

    async def _set_room_panel_context(self, room_id: int, user_id: int, context: str) -> None:
        async with self._database.session() as session, session.begin():
            panel = await session.scalar(
                select(RoomPanel).where(RoomPanel.room_id == room_id, RoomPanel.user_id == user_id)
            )
            if panel is not None:
                panel.context = context

    async def _home_panel(self, user_id: int) -> HomePanel | None:
        async with self._database.session() as session:
            return await session.get(HomePanel, user_id)

    async def _has_room_panels(self, user_id: int) -> bool:
        async with self._database.session() as session:
            return (
                await session.scalar(
                    select(RoomPanel.id).where(RoomPanel.user_id == user_id).limit(1)
                )
            ) is not None

    async def _has_other_room_panels(self, user_id: int, room_id: int) -> bool:
        async with self._database.session() as session:
            return (
                await session.scalar(
                    select(RoomPanel.id)
                    .where(RoomPanel.user_id == user_id, RoomPanel.room_id != room_id)
                    .limit(1)
                )
            ) is not None

    async def _retire_home_panel(self, user_id: int) -> None:
        panel = await self._home_panel(user_id)
        if panel is None:
            return
        await self._retire_message(user_id, panel.message_id)
        async with self._database.session() as session, session.begin():
            await session.execute(delete(HomePanel).where(HomePanel.user_id == user_id))

    async def _retire_room_panels(self, user_id: int) -> None:
        async with self._database.session() as session:
            panels = list(
                await session.scalars(select(RoomPanel).where(RoomPanel.user_id == user_id))
            )
        for panel in panels:
            await self._retire_message(user_id, panel.message_id)
        if panels:
            async with self._database.session() as session, session.begin():
                await session.execute(delete(RoomPanel).where(RoomPanel.user_id == user_id))

    async def _retire_message(self, user_id: int, message_id: int) -> None:
        try:
            await self._bot.delete_message(user_id, message_id)
        except (TelegramBadRequest, TelegramForbiddenError):
            pass

    async def _delete_temporary_message(
        self, user_id: int, message_id: int, ttl_seconds: float
    ) -> None:
        await asyncio.sleep(ttl_seconds)
        await self._retire_message(user_id, message_id)

    async def _mark_delivery_failure(self, room_id: int, user_id: int) -> None:
        try:
            if await self._rooms.room_game_type(room_id) == GAME_TYPE:
                await self._hats.mark_delivery_failure(room_id, user_id)
            else:
                await self._rooms.mark_delivery_failure(room_id, user_id)
        except DomainError:
            return

    async def _save_room_panel(
        self, room_id: int, user_id: int, message_id: int, context: str
    ) -> None:
        async with self._database.session() as session, session.begin():
            panel = await session.scalar(
                select(RoomPanel).where(RoomPanel.room_id == room_id, RoomPanel.user_id == user_id)
            )
            if panel is None:
                session.add(
                    RoomPanel(
                        room_id=room_id,
                        user_id=user_id,
                        message_id=message_id,
                        context=context,
                    )
                )
            else:
                panel.message_id = message_id
                panel.context = context

    async def _save_home_panel(self, user_id: int, message_id: int, context: str) -> None:
        async with self._database.session() as session, session.begin():
            panel = await session.get(HomePanel, user_id)
            if panel is None:
                session.add(HomePanel(user_id=user_id, message_id=message_id, context=context))
            else:
                panel.message_id = message_id
                panel.context = context
