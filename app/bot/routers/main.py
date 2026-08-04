from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

from app.bot.callbacks import RoomCallback, unpack_room_callback
from app.bot.states import JoinByCode
from app.domain.errors import DomainError
from app.services.delivery_service import DeliveryService
from app.services.room_service import RoomService
from app.services.views import RoomView


def create_main_router(rooms: RoomService, delivery: DeliveryService) -> Router:
    router = Router(name="main")

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        await _ensure_telegram_user(rooms, message.from_user)
        await state.clear()
        parts = (message.text or "").split(maxsplit=1)
        argument = parts[1] if len(parts) == 2 else ""
        if argument.startswith("join_"):
            try:
                room_id = await rooms.join_by_token(
                    argument.removeprefix("join_"), message.from_user.id
                )
            except DomainError:
                await delivery.render_home_notice(
                    message.from_user.id, "Не удалось войти в комнату по этой ссылке."
                )
                return
            await delivery.render_room(room_id)
            return
        await delivery.render_home(message.from_user.id)

    @router.callback_query(F.data == "h:new")
    async def create_room(callback: CallbackQuery) -> None:
        if callback.from_user is None:
            return
        await _ensure_telegram_user(rooms, callback.from_user)
        room_id = await rooms.create_spy_room(callback.from_user.id)
        await callback.answer("Комната создана")
        await delivery.render_room(room_id)

    @router.callback_query(F.data == "h:join")
    async def ask_for_code(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.from_user is None:
            return
        await state.set_state(JoinByCode.waiting_for_code)
        await callback.answer("Отправьте код комнаты одним сообщением")
        await delivery.render_home_notice(
            callback.from_user.id, "Отправьте код комнаты одним сообщением."
        )

    @router.message(JoinByCode.waiting_for_code, F.text)
    async def join_by_code(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        await _ensure_telegram_user(rooms, message.from_user)
        try:
            room_id = await rooms.join_by_code(message.text or "", message.from_user.id)
        except DomainError as error:
            await delivery.render_home_notice(message.from_user.id, str(error))
            return
        await state.clear()
        await delivery.render_room(room_id)

    @router.callback_query(F.data.startswith("r:"))
    async def room_callback(callback: CallbackQuery) -> None:
        if callback.from_user is None or callback.data is None:
            return
        parsed = unpack_room_callback(callback.data)
        if parsed is None:
            await callback.answer("Неизвестное действие.")
            return
        await _ensure_telegram_user(rooms, callback.from_user)
        try:
            view = await rooms.get_room_view(parsed.room_id, callback.from_user.id)
        except DomainError as error:
            await callback.answer(str(error))
            return
        if view.revision != parsed.revision:
            await callback.answer("Панель устарела — обновляю её.")
            await delivery.render_room_for_user(parsed.room_id, callback.from_user.id)
            return
        try:
            handled_locally = await _handle_room_callback(
                rooms, delivery, parsed, view, callback.from_user.id
            )
        except (DomainError, ValueError) as error:
            await callback.answer(str(error))
            return
        await callback.answer()
        if not handled_locally:
            await delivery.render_room(parsed.room_id)

    return router


async def _handle_room_callback(
    rooms: RoomService,
    delivery: DeliveryService,
    callback: RoomCallback,
    view: RoomView,
    user_id: int,
) -> bool:
    action = callback.action
    if action == "noop":
        return True
    if action == "main":
        await delivery.render_room_for_user(callback.room_id, user_id)
        return True
    if action == "themes":
        await delivery.render_theme_selector(
            callback.room_id, user_id, int(callback.argument or "0")
        )
        return True
    if action == "candidates":
        await delivery.render_candidate_selector(
            callback.room_id, user_id, int(callback.argument or "0")
        )
        return True
    if action == "kicks":
        await delivery.render_kick_selector(
            callback.room_id, user_id, int(callback.argument or "0")
        )
        return True
    if action == "open":
        await rooms.set_join_open(callback.room_id, user_id, callback.argument == "1")
    elif action == "mode":
        await rooms.set_spy_count_mode(callback.room_id, user_id, callback.argument)
    elif action == "spy_delta":
        settings = view.settings
        count = int(settings.get("spy_count", 1)) + int(callback.argument)
        await rooms.set_exact_spy_count(callback.room_id, user_id, count)
    elif action == "know":
        settings = view.settings
        await rooms.set_spies_know_each_other(
            callback.room_id, user_id, not bool(settings.get("spies_know_each_other"))
        )
    elif action == "theme":
        await rooms.toggle_theme(callback.room_id, user_id, int(callback.argument))
    elif action == "kick":
        await rooms.remove_lobby_member(callback.room_id, user_id, int(callback.argument))
    elif action == "start":
        await rooms.start_spy_game(callback.room_id, user_id)
    elif action == "candidate":
        await rooms.propose_exile(callback.room_id, user_id, int(callback.argument))
    elif action == "reveal":
        await rooms.begin_reveal(callback.room_id, user_id)
    elif action == "vote":
        vote_id, raw_choice = callback.argument.split(":", 1)
        await rooms.cast_vote(callback.room_id, user_id, int(vote_id), raw_choice == "1")
    elif action == "leave":
        await rooms.leave_room(callback.room_id, user_id)
    elif action == "cancel":
        await rooms.emergency_cancel(callback.room_id, user_id)
    else:
        raise DomainError("Неизвестное действие.")
    return False


async def _ensure_telegram_user(rooms: RoomService, user: User) -> None:
    display_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    await rooms.ensure_user(user.id, display_name or str(user.id), user.username)
