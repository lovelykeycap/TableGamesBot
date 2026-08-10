from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import RoomCallback, unpack_room_callback
from app.bot.states import HatInput, JoinByCode
from app.domain.errors import DomainError
from app.domain.games.hat import GAME_TYPE
from app.domain.games.spy import ROLE_CIVILIAN, VOTE_EXILE
from app.services.delivery_service import DeliveryService
from app.services.hat_service import HatAppealOutcome, HatLastWordClaimOutcome, HatService
from app.services.room_service import RoomService
from app.services.views import HatRoomView, RoomView


def create_main_router(rooms: RoomService, hats: HatService, delivery: DeliveryService) -> Router:
    router = Router(name="main")

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        await _ensure_telegram_user(rooms, message.from_user)
        await state.clear()
        await delivery.delete_processed_message(message)
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

    @router.callback_query(F.data.in_({"h:new", "h:new:spy", "h:new:hat"}))
    async def create_room(callback: CallbackQuery) -> None:
        if callback.from_user is None:
            return
        await _ensure_telegram_user(rooms, callback.from_user)
        room_id = (
            await hats.create_room(callback.from_user.id)
            if callback.data == "h:new:hat"
            else await rooms.create_spy_room(callback.from_user.id)
        )
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

    @router.callback_query(F.data == "h:rooms")
    async def show_my_rooms(callback: CallbackQuery) -> None:
        if callback.from_user is None:
            return
        await _ensure_telegram_user(rooms, callback.from_user)
        summaries = await rooms.open_rooms_for_user(callback.from_user.id)
        if not summaries:
            await callback.answer("У вас нет активных комнат.")
            return
        if len(summaries) == 1:
            await callback.answer()
            await delivery.render_room_for_user(summaries[0].id, callback.from_user.id)
            return
        builder = InlineKeyboardBuilder()
        for summary in summaries:
            status_label = "партия" if summary.status == "playing" else "лобби"
            builder.button(
                text=f"■ {_game_title(summary.game_type)} · {summary.join_code} — {status_label}",
                callback_data=f"h:open:{summary.id}",
            )
        builder.button(text="← К играм", callback_data="h:home")
        builder.adjust(1)
        await callback.answer()
        await delivery.render_custom_home(
            callback.from_user.id,
            "<b>Активные комнаты</b>\nВыберите комнату, к которой хотите вернуться.",
            builder.as_markup(),
            context="home",
        )

    @router.callback_query(F.data.startswith("h:open:"))
    async def open_my_room(callback: CallbackQuery) -> None:
        if callback.from_user is None or callback.data is None:
            return
        try:
            room_id = int(callback.data.rsplit(":", 1)[1])
            summaries = await rooms.open_rooms_for_user(callback.from_user.id)
            if room_id not in {summary.id for summary in summaries}:
                raise DomainError("Эта комната больше недоступна.")
        except (DomainError, ValueError) as error:
            await callback.answer(str(error))
            return
        await callback.answer()
        await delivery.render_room_for_user(room_id, callback.from_user.id)

    @router.callback_query(F.data == "h:home")
    async def return_home(callback: CallbackQuery) -> None:
        if callback.from_user is None:
            return
        await callback.answer()
        await delivery.render_home(callback.from_user.id)

    @router.message(Command("leave"))
    async def leave_hat_room(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        await _ensure_telegram_user(rooms, message.from_user)
        await state.clear()
        await delivery.delete_processed_message(message)
        raw_code = (message.text or "").partition(" ")[2].strip().upper()
        candidates = await _active_hat_rooms_for_user(rooms, hats, message.from_user.id)
        if raw_code:
            candidates = [view for view in candidates if view.join_code == raw_code]
        if not candidates:
            await delivery.render_home_notice(
                message.from_user.id, "Активная комната «Шляпы» не найдена."
            )
            return
        if len(candidates) == 1:
            await _render_hat_leave_confirmation(delivery, message.from_user.id, candidates[0])
            return
        builder = InlineKeyboardBuilder()
        for view in candidates:
            builder.button(
                text=f"🎩 {view.join_code} — {view.status}", callback_data=f"h:leave:{view.room_id}"
            )
        builder.button(text="← К играм", callback_data="h:home")
        builder.adjust(1)
        await delivery.render_custom_home(
            message.from_user.id,
            "<b>Из какой комнаты выйти?</b>",
            builder.as_markup(),
            context="home",
        )

    @router.callback_query(F.data.startswith("h:leave:"))
    async def leave_hat_callback(callback: CallbackQuery) -> None:
        if callback.from_user is None or callback.data is None:
            return
        parts = callback.data.split(":")
        if len(parts) not in {3, 4}:
            await callback.answer("Некорректное действие.")
            return
        try:
            room_id = int(parts[2])
            candidates = await _active_hat_rooms_for_user(rooms, hats, callback.from_user.id)
            view = next(candidate for candidate in candidates if candidate.room_id == room_id)
        except (DomainError, StopIteration, ValueError):
            await callback.answer("Эта комната больше недоступна.")
            return
        if len(parts) == 3:
            await callback.answer()
            await _render_hat_leave_confirmation(delivery, callback.from_user.id, view)
            return
        if parts[3] == "cancel":
            await callback.answer()
            await delivery.render_room_for_user(room_id, callback.from_user.id)
            return
        if parts[3] != "confirm":
            await callback.answer("Некорректное действие.")
            return
        try:
            await hats.leave_room(room_id, callback.from_user.id)
        except DomainError as error:
            await callback.answer(str(error))
            return
        await callback.answer("Вы вышли из комнаты.")
        await delivery.render_room(room_id)
        await delivery.render_home(callback.from_user.id)

    @router.message(JoinByCode.waiting_for_code, F.text, ~F.text.startswith("/"))
    async def join_by_code(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        await _ensure_telegram_user(rooms, message.from_user)
        await delivery.delete_processed_message(message)
        try:
            room_id = await rooms.join_by_code(message.text or "", message.from_user.id)
        except DomainError as error:
            await delivery.render_home_notice(message.from_user.id, str(error))
            return
        await state.clear()
        await delivery.render_room(room_id)

    @router.message(HatInput.waiting_for_word, F.text, ~F.text.startswith("/"))
    async def accept_hat_word(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        data = await state.get_data()
        room_id = int(data["room_id"])
        word_id = data.get("word_id")
        await delivery.delete_processed_message(message)
        try:
            if word_id is None:
                await hats.add_word(room_id, message.from_user.id, message.text or "")
            else:
                await hats.edit_word(
                    room_id, message.from_user.id, int(word_id), message.text or ""
                )
        except DomainError as error:
            await delivery.render_hat_word_prompt(
                room_id, message.from_user.id, word_id=word_id, error=str(error)
            )
            return
        await state.clear()
        await delivery.release_hat_word_prompt(room_id, message.from_user.id)
        await delivery.render_room(room_id)

    @router.message(HatInput.waiting_for_emergency_phrase, F.text, ~F.text.startswith("/"))
    async def confirm_hat_emergency_cancel(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        data = await state.get_data()
        room_id = int(data["room_id"])
        await delivery.delete_processed_message(message)
        if (message.text or "") != "Выход":
            await delivery.render_hat_cancel_phrase(
                room_id, message.from_user.id, error="Нужно написать ровно «Выход»."
            )
            return
        try:
            await hats.emergency_cancel(room_id, message.from_user.id)
        except DomainError:
            await delivery.render_hat_cancel_phrase(
                room_id, message.from_user.id, error="Отмена больше недоступна."
            )
            return
        await state.clear()
        await delivery.render_room(room_id)

    @router.message(F.chat.type == "private", F.text.startswith("/"))
    async def discard_unknown_command(message: Message) -> None:
        await delivery.delete_processed_message(message)

    @router.callback_query(F.data.startswith("r:"))
    async def room_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.from_user is None or callback.data is None:
            return
        parsed = unpack_room_callback(callback.data)
        if parsed is None:
            await callback.answer("Неизвестное действие.")
            return
        await _ensure_telegram_user(rooms, callback.from_user)
        try:
            game_type = await rooms.room_game_type(parsed.room_id)
            if game_type == GAME_TYPE:
                hat_view = await hats.get_room_view(parsed.room_id, callback.from_user.id)
                if hat_view.revision != parsed.revision:
                    await callback.answer("Панель устарела — обновляю её.")
                    await delivery.render_room_for_user(parsed.room_id, callback.from_user.id)
                    return
                handled_locally = await _handle_hat_callback(
                    hats, delivery, state, parsed, hat_view, callback.from_user.id
                )
                await callback.answer()
                if not handled_locally:
                    await delivery.render_room(parsed.room_id)
                return
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
        outcome = await rooms.cast_vote(callback.room_id, user_id, int(vote_id), raw_choice == "1")
        if (
            outcome is not None
            and outcome.kind == VOTE_EXILE
            and outcome.accepted
            and outcome.subject_role == ROLE_CIVILIAN
        ):
            await delivery.render_room(callback.room_id)
            await delivery.announce_room_notice(
                callback.room_id,
                "<b>Голосование завершено</b>\n"
                f"Игрок <b>{escape(outcome.subject_name)}</b> не был шпионом.",
            )
            return True
    elif action == "leave":
        await rooms.leave_room(callback.room_id, user_id)
    elif action == "cancel":
        await rooms.emergency_cancel(callback.room_id, user_id)
    else:
        raise DomainError("Неизвестное действие.")
    return False


async def _handle_hat_callback(
    hats: HatService,
    delivery: DeliveryService,
    state: FSMContext,
    callback: RoomCallback,
    view: HatRoomView,
    user_id: int,
) -> bool:
    action = callback.action
    if action == "noop":
        return True
    if action == "hat_main":
        await state.clear()
        await delivery.release_hat_word_prompt(callback.room_id, user_id)
        await delivery.render_room_for_user(callback.room_id, user_id)
        return True
    if action == "hat_order_manual":
        await delivery.render_hat_manual_order(callback.room_id, user_id)
        return True
    if action == "hat_random":
        await hats.randomize_seats(callback.room_id, user_id)
        await delivery.render_hat_random_order(callback.room_id, user_id)
        return True
    if action == "hat_seat":
        await hats.select_manual_seat(callback.room_id, user_id, int(callback.argument))
        await delivery.render_hat_manual_order(callback.room_id, user_id)
        return True
    if action == "hat_undo":
        await hats.undo_manual_seat(callback.room_id, user_id)
        await delivery.render_hat_manual_order(callback.room_id, user_id)
        return True
    if action == "hat_order_ok":
        await hats.confirm_seats(callback.room_id, user_id)
        return False
    if action == "hat_add":
        await state.set_state(HatInput.waiting_for_word)
        await state.update_data(room_id=callback.room_id, word_id=None)
        await delivery.render_hat_word_prompt(callback.room_id, user_id)
        return True
    if action == "hat_word":
        await delivery.render_hat_word_editor(callback.room_id, user_id, int(callback.argument))
        return True
    if action == "hat_cards_page":
        await delivery.render_hat_word_list(
            callback.room_id, user_id, int(callback.argument or "0")
        )
        return True
    if action == "hat_top":
        await delivery.render_hat_top(callback.room_id, user_id)
        return True
    if action == "hat_edit":
        await state.set_state(HatInput.waiting_for_word)
        await state.update_data(room_id=callback.room_id, word_id=int(callback.argument))
        await delivery.render_hat_word_prompt(
            callback.room_id, user_id, word_id=int(callback.argument)
        )
        return True
    if action == "hat_delete":
        await hats.delete_word(callback.room_id, user_id, int(callback.argument))
        return False
    if action == "hat_appeals":
        await delivery.render_hat_appeal_selector(
            callback.room_id, user_id, int(callback.argument or "0")
        )
        return True
    if action == "hat_appeal":
        outcome = await hats.start_appeal(callback.room_id, user_id, int(callback.argument))
        if outcome is not None:
            await delivery.render_room(callback.room_id)
            await _announce_hat_appeal_outcome(delivery, callback.room_id, outcome)
            return True
        return False
    if action == "hat_vote":
        appeal_id, raw_choice = callback.argument.split(":", 1)
        outcome = await hats.cast_appeal_vote(
            callback.room_id, user_id, int(appeal_id), raw_choice == "1"
        )
        if outcome is not None:
            await delivery.render_room(callback.room_id)
            await _announce_hat_appeal_outcome(delivery, callback.room_id, outcome)
            return True
        return False
    if action == "hat_last_word":
        outcome = await hats.start_last_word_claim(callback.room_id, user_id)
        if outcome is not None:
            await delivery.render_room(callback.room_id)
            await _announce_hat_last_word_claim_outcome(delivery, callback.room_id, outcome)
            return True
        return False
    if action == "hat_last_word_vote":
        claim_id, raw_choice = callback.argument.split(":", 1)
        outcome = await hats.cast_last_word_claim_vote(
            callback.room_id, user_id, int(claim_id), raw_choice == "1"
        )
        if outcome is not None:
            await delivery.render_room(callback.room_id)
            await _announce_hat_last_word_claim_outcome(delivery, callback.room_id, outcome)
            return True
        return False
    if action == "hat_violation":
        await delivery.render_hat_violation_confirm(callback.room_id, user_id)
        return True
    if action == "hat_cancel":
        await delivery.render_hat_cancel_confirm(callback.room_id, user_id)
        return True
    if action == "hat_cancel_text":
        await state.set_state(HatInput.waiting_for_emergency_phrase)
        await state.update_data(room_id=callback.room_id)
        await delivery.render_hat_cancel_phrase(callback.room_id, user_id)
        return True

    if action == "hat_open":
        await hats.set_join_open(callback.room_id, user_id, callback.argument == "1")
    elif action == "hat_words":
        await hats.set_words_per_player(
            callback.room_id, user_id, view.words_per_player + int(callback.argument)
        )
    elif action == "hat_time":
        round_number, delta = callback.argument.split(":", 1)
        current = view.round_seconds[int(round_number) - 1]
        await hats.set_round_seconds(
            callback.room_id, user_id, int(round_number), current + int(delta)
        )
    elif action == "hat_collect":
        await hats.start_collection(callback.room_id, user_id)
    elif action == "hat_round":
        await hats.start_round(callback.room_id, user_id)
    elif action == "hat_ready":
        await hats.set_ready(callback.room_id, user_id, callback.argument == "1")
    elif action == "hat_correct":
        await hats.mark_correct(callback.room_id, user_id)
    elif action == "hat_violation_ok":
        await hats.confirm_violation(callback.room_id, user_id)
    elif action == "hat_finish":
        await hats.finish_game(callback.room_id, user_id)
    else:
        raise DomainError("Неизвестное действие.")
    return False


async def _announce_hat_appeal_outcome(
    delivery: DeliveryService, room_id: int, outcome: HatAppealOutcome
) -> None:
    verdict = "очки сняты" if outcome.accepted else "очки остаются"
    await delivery.announce_room_notice(
        room_id,
        "<b>Апелляция завершена</b>\n"
        f"Карточка <b>{escape(outcome.card_text)}</b>: {verdict} "
        f"у пары {escape(outcome.explainer_name)} → {escape(outcome.guesser_name)}.",
    )


async def _announce_hat_last_word_claim_outcome(
    delivery: DeliveryService, room_id: int, outcome: HatLastWordClaimOutcome
) -> None:
    verdict = "слово засчитано, пара получает по очку" if outcome.accepted else "слово не засчитано"
    await delivery.announce_room_notice(
        room_id,
        "<b>Решение по последнему слову</b>\n"
        f"Карточка <b>{escape(outcome.card_text)}</b>: {verdict} "
        f"у пары {escape(outcome.explainer_name)} → {escape(outcome.guesser_name)}.",
    )


async def _ensure_telegram_user(rooms: RoomService, user: User) -> None:
    display_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    await rooms.ensure_user(user.id, display_name or str(user.id), user.username)


async def _active_hat_rooms_for_user(
    rooms: RoomService, hats: HatService, user_id: int
) -> list[HatRoomView]:
    result: list[HatRoomView] = []
    for summary in await rooms.open_rooms_for_user(user_id):
        if summary.game_type != GAME_TYPE:
            continue
        view = await hats.get_room_view(summary.id, user_id)
        if view.actor_status == "active":
            result.append(view)
    return result


async def _render_hat_leave_confirmation(
    delivery: DeliveryService, user_id: int, view: HatRoomView
) -> None:
    builder = InlineKeyboardBuilder()
    builder.button(text="🚪 Да, выйти", callback_data=f"h:leave:{view.room_id}:confirm")
    builder.button(text="← Остаться", callback_data=f"h:leave:{view.room_id}:cancel")
    builder.adjust(1)
    await delivery.render_custom_home(
        user_id,
        "<b>Выйти из комнаты «Шляпа»?</b>\n"
        "Во время партии вы станете зрителем, а ваши очки и карточки останутся в истории.",
        builder.as_markup(),
        context="home",
    )


def _game_title(game_type: str) -> str:
    return {"spy": "Шпион", "hat": "Шляпа"}.get(game_type, game_type)
