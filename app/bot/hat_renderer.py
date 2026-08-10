from __future__ import annotations

from html import escape
from math import ceil

from aiogram.types import CopyTextButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import pack_room_callback
from app.domain.games.hat import (
    PHASE_APPEAL,
    PHASE_COLLECTING,
    PHASE_CONFIGURED,
    PHASE_COUNTDOWN,
    PHASE_FINAL_BREAK,
    PHASE_FINISHED,
    PHASE_LAST_WORD_CLAIM,
    PHASE_READY,
    PHASE_ROUND_BREAK,
    PHASE_TURN,
)
from app.services.views import HatRoomView, HatScoreView, HatWordView, MemberView


def _name(value: str) -> str:
    return escape(value[:80])


class HatRenderer:
    def __init__(self, bot_username: str) -> None:
        self._bot_username = bot_username

    def render(self, view: HatRoomView) -> tuple[str, InlineKeyboardMarkup]:
        if view.status == "finished" or view.phase == PHASE_FINISHED:
            return self._render_finished(view)
        if view.status == "lobby" or view.phase == PHASE_CONFIGURED:
            return self._render_lobby(view)
        if view.phase == PHASE_COLLECTING:
            return self._render_collecting(view)
        if view.phase == PHASE_APPEAL:
            return self._render_appeal(view)
        if view.phase == PHASE_LAST_WORD_CLAIM:
            return self._render_last_word_claim(view)
        if view.phase in {PHASE_ROUND_BREAK, PHASE_FINAL_BREAK}:
            return self._render_break(view)
        return self._render_turn(view)

    def render_word_list(self, view: HatRoomView, page: int) -> tuple[str, InlineKeyboardMarkup]:
        return self._render_collecting(view, page=page)

    def render_top(self, view: HatRoomView) -> tuple[str, InlineKeyboardMarkup]:
        builder = InlineKeyboardBuilder()
        builder.button(text="← К игре", callback_data=self._cb(view, "hat_main"))
        return (
            "<b>🎩 Шляпа — топ игроков</b>\n\n" + "\n".join(self._score_lines(view.scores)),
            builder.as_markup(),
        )

    def render_word_prompt(
        self, view: HatRoomView, *, editing: HatWordView | None = None, error: str | None = None
    ) -> tuple[str, InlineKeyboardMarkup]:
        if editing is None:
            text = (
                "<b>Добавить карточку</b>\n"
                "Отправьте одно имя или слово сообщением. Оно будет удалено из чата."
            )
        else:
            text = (
                "<b>Изменить карточку</b>\n"
                f"Сейчас: <b>{_name(editing.text)}</b>\n"
                "Отправьте новый вариант одним сообщением. Оно будет удалено из чата."
            )
        builder = InlineKeyboardBuilder()
        builder.button(text="← К карточкам", callback_data=self._cb(view, "hat_main"))
        return (f"<b>{_name(error)}</b>\n\n{text}" if error else text), builder.as_markup()

    def render_word_editor(
        self, view: HatRoomView, word: HatWordView
    ) -> tuple[str, InlineKeyboardMarkup]:
        builder = InlineKeyboardBuilder()
        builder.button(text="✏️ Изменить", callback_data=self._cb(view, "hat_edit", str(word.id)))
        builder.button(text="🗑 Удалить", callback_data=self._cb(view, "hat_delete", str(word.id)))
        builder.button(text="← К карточкам", callback_data=self._cb(view, "hat_main"))
        builder.adjust(2, 1)
        return f"<b>Карточка</b>\n{_name(word.text)}", builder.as_markup()

    def render_manual_order(self, view: HatRoomView) -> tuple[str, InlineKeyboardMarkup]:
        seat_ids = {seat.member_id for seat in view.seats}
        unchosen = [member for member in view.active_members if member.id not in seat_ids]
        lines = ["<b>Порядок игроков</b>", "Нажимайте игроков по порядку их мест в круге.", ""]
        lines.extend(self._seat_lines(view))
        builder = InlineKeyboardBuilder()
        for member in unchosen:
            builder.button(
                text=_name(member.display_name),
                callback_data=self._cb(view, "hat_seat", str(member.id)),
            )
        builder.adjust(1)
        if view.seats:
            builder.button(text="↩️ Отменить последний", callback_data=self._cb(view, "hat_undo"))
        if not unchosen and len(view.seats) >= 3:
            builder.button(
                text="✅ Подтвердить порядок", callback_data=self._cb(view, "hat_order_ok")
            )
        builder.button(text="🎲 Перемешать", callback_data=self._cb(view, "hat_random"))
        builder.button(text="← К лобби", callback_data=self._cb(view, "hat_main"))
        builder.adjust(1)
        return "\n".join(lines), builder.as_markup()

    def render_random_order(self, view: HatRoomView) -> tuple[str, InlineKeyboardMarkup]:
        lines = ["<b>Случайный порядок</b>", ""] + self._seat_lines(view)
        builder = InlineKeyboardBuilder()
        builder.button(text="🔀 Ещё раз", callback_data=self._cb(view, "hat_random"))
        builder.button(text="✅ Подтвердить порядок", callback_data=self._cb(view, "hat_order_ok"))
        builder.button(
            text="✋ Расставить вручную", callback_data=self._cb(view, "hat_order_manual")
        )
        builder.adjust(1)
        return "\n".join(lines), builder.as_markup()

    def render_appeal_selector(
        self, view: HatRoomView, page: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        cards, page, pages = self._page(view.appealable_cards, page)
        lines = ["<b>Апелляция</b>", "Выберите засчитанную карточку последнего хода."]
        if not cards:
            lines.append("Доступных для апелляции карточек нет.")
        builder = InlineKeyboardBuilder()
        for card in cards:
            builder.button(
                text=f"{card.text[:45]} · {card.explainer_name} → {card.guesser_name}",
                callback_data=self._cb(view, "hat_appeal", str(card.id)),
            )
        builder.adjust(1)
        self._pagination(builder, view, "hat_appeals", page, pages)
        builder.button(text="← К игре", callback_data=self._cb(view, "hat_main"))
        builder.adjust(1)
        return "\n".join(lines), builder.as_markup()

    def render_violation_confirm(self, view: HatRoomView) -> tuple[str, InlineKeyboardMarkup]:
        builder = InlineKeyboardBuilder()
        builder.button(text="Да, исключить", callback_data=self._cb(view, "hat_violation_ok"))
        builder.button(text="Нет, продолжить", callback_data=self._cb(view, "hat_main"))
        builder.adjust(2)
        return "<b>Исключить слово без очков?</b>", builder.as_markup()

    def render_cancel_confirm(self, view: HatRoomView) -> tuple[str, InlineKeyboardMarkup]:
        builder = InlineKeyboardBuilder()
        builder.button(text="Продолжить отмену", callback_data=self._cb(view, "hat_cancel_text"))
        builder.button(text="← К игре", callback_data=self._cb(view, "hat_main"))
        builder.adjust(1)
        return (
            "<b>Аварийно завершить партию?</b>\n"
            "Это действие нельзя отменить. На следующем шаге потребуется написать «Выход»."
        ), builder.as_markup()

    def render_cancel_phrase(
        self, view: HatRoomView, error: str | None = None
    ) -> tuple[str, InlineKeyboardMarkup]:
        prefix = f"<b>{_name(error)}</b>\n\n" if error else ""
        builder = InlineKeyboardBuilder()
        builder.button(text="← К игре", callback_data=self._cb(view, "hat_main"))
        return (
            prefix + "<b>Подтвердите аварийное завершение</b>\n"
            "Отправьте ровно слово <code>Выход</code>. Сообщение будет удалено."
        ), builder.as_markup()

    def _render_lobby(self, view: HatRoomView) -> tuple[str, InlineKeyboardMarkup]:
        link = f"https://t.me/{self._bot_username}?start=join_{view.join_token}"
        lines = [
            "<b>🎩 Шляпа — лобби</b>",
            f"Код комнаты: <code>{view.join_code}</code>",
            "",
            f"Игроки ({len(view.active_members)}/40):",
            *self._member_lines(view.active_members),
            "",
            f"Карточек от игрока: {view.words_per_player}",
            "Время раундов: " + " · ".join(f"{value} с" for value in view.round_seconds),
            f"Вход: {'открыт' if view.is_join_open else 'закрыт'}",
            "",
            "Порядок: "
            + ("заполнен" if len(view.seats) == len(view.active_members) else "не задан"),
        ]
        if view.is_host:
            lines.extend(["", "Вы хост комнаты."])
        builder = InlineKeyboardBuilder()
        builder.button(text="📋 Скопировать код", copy_text=CopyTextButton(text=view.join_code))
        builder.button(text="🔗 Скопировать ссылку", copy_text=CopyTextButton(text=link))
        builder.adjust(2)
        if view.is_host:
            builder.button(
                text="🔒 Закрыть вход" if view.is_join_open else "🔓 Открыть вход",
                callback_data=self._cb(view, "hat_open", "0" if view.is_join_open else "1"),
            )
            builder.button(text="−", callback_data=self._cb(view, "hat_words", "-1"))
            builder.button(
                text=f"Карточек: {view.words_per_player}", callback_data=self._cb(view, "noop")
            )
            builder.button(text="+", callback_data=self._cb(view, "hat_words", "1"))
            builder.adjust(1, 3)
            for number, seconds in enumerate(view.round_seconds, start=1):
                builder.button(text="−", callback_data=self._cb(view, "hat_time", f"{number}:-10"))
                builder.button(
                    text=f"Раунд {number}: {seconds} с", callback_data=self._cb(view, "noop")
                )
                builder.button(text="+", callback_data=self._cb(view, "hat_time", f"{number}:10"))
                builder.adjust(3)
            builder.button(
                text="🪑 Расставить вручную", callback_data=self._cb(view, "hat_order_manual")
            )
            builder.button(text="🎲 Случайный порядок", callback_data=self._cb(view, "hat_random"))
            builder.button(
                text="📝 Начать сбор карточек", callback_data=self._cb(view, "hat_collect")
            )
            builder.adjust(1)
        return "\n".join(lines), builder.as_markup()

    def _render_collecting(
        self, view: HatRoomView, *, page: int = 0
    ) -> tuple[str, InlineKeyboardMarkup]:
        submitted = dict(view.submission_counts)
        lines = [
            "<b>🎩 Шляпа — сбор карточек</b>",
            f"Осталось: {view.collection_seconds_remaining or 0} с",
            "",
            "Прогресс:",
            *[
                f"• {_name(member.display_name)} — "
                f"{submitted.get(member.id, 0)}/{view.words_per_player}"
                for member in view.active_members
            ],
        ]
        builder = InlineKeyboardBuilder()
        if view.actor_status == "active":
            if len(view.my_words) < view.words_per_player:
                builder.button(text="➕ Добавить карточку", callback_data=self._cb(view, "hat_add"))
            words, page, pages = self._page(view.my_words, page)
            for word in words:
                builder.button(
                    text=f"📝 {word.text[:55]}",
                    callback_data=self._cb(view, "hat_word", str(word.id)),
                )
            builder.adjust(1)
            self._pagination(builder, view, "hat_cards_page", page, pages)
        if view.is_host and view.actor_status == "active":
            builder.button(text="⚠️ Аварийно завершить", callback_data=self._cb(view, "hat_cancel"))
            builder.adjust(1)
        return "\n".join(lines), builder.as_markup()

    def _render_turn(self, view: HatRoomView) -> tuple[str, InlineKeyboardMarkup]:
        turn = view.turn
        if turn is None:
            return (
                "<b>🎩 Шляпа</b>\nПодготавливаем следующий ход…",
                InlineKeyboardBuilder().as_markup(),
            )
        lines = [
            f"<b>🎩 Шляпа — раунд {view.round_number}</b>",
            f"Объясняет: <b>{_name(turn.explainer_name)}</b>",
            f"Отгадывает: <b>{_name(turn.guesser_name)}</b>",
        ]
        if view.actor_status != "active":
            lines.append("Вы зритель. Управлять партией уже нельзя.")
        actor_in_pair = view.actor_member_id in {turn.explainer_member_id, turn.guesser_member_id}
        if view.phase == PHASE_READY:
            lines.append("\nОба игрока должны подтвердить готовность.")
        elif view.phase == PHASE_COUNTDOWN:
            lines.append(
                f"\nСтарт через: <b>{turn.seconds_remaining or 0}</b>"
                if actor_in_pair
                else "\nПара начинает ход."
            )
        else:
            if view.current_card_text:
                lines.extend(["", f"<b>{_name(view.current_card_text)}</b>"])
            if actor_in_pair:
                lines.append(f"Время: <b>{turn.seconds_remaining or 0} с</b>")
        builder = InlineKeyboardBuilder()
        if view.phase == PHASE_READY and actor_in_pair:
            ready = (
                turn.explainer_ready
                if view.actor_member_id == turn.explainer_member_id
                else turn.guesser_ready
            )
            builder.button(
                text="↩️ Отменить готовность" if ready else "✅ Готов",
                callback_data=self._cb(view, "hat_ready", "0" if ready else "1"),
            )
        if view.phase == PHASE_READY and view.actor_status == "active":
            builder.button(text="🏆 Посмотреть топ", callback_data=self._cb(view, "hat_top"))
            if view.appealable_cards:
                builder.button(text="⚖️ Апелляция", callback_data=self._cb(view, "hat_appeals", "0"))
            if view.last_word_claim_card_id is not None:
                builder.button(
                    text="⏱️ Я успел объяснить последнее слово",
                    callback_data=self._cb(view, "hat_last_word"),
                )
        elif view.phase == PHASE_TURN and view.actor_member_id == turn.explainer_member_id:
            builder.button(text="✅ Слово отгадано", callback_data=self._cb(view, "hat_correct"))
            builder.button(
                text="⚠️ Я нарушил правила", callback_data=self._cb(view, "hat_violation")
            )
        if view.is_host and view.actor_status == "active":
            builder.button(text="⚠️ Аварийно завершить", callback_data=self._cb(view, "hat_cancel"))
        builder.adjust(1)
        return "\n".join(lines), builder.as_markup()

    def _render_break(self, view: HatRoomView) -> tuple[str, InlineKeyboardMarkup]:
        final = view.phase == PHASE_FINAL_BREAK
        next_round = view.round_number + 1 if view.round_has_cards else view.round_number
        title = "Финальный перерыв" if final else f"Перерыв перед раундом {next_round}"
        lines = [
            f"<b>🎩 Шляпа — {title}</b>",
            "",
            "<b>Топ игроков</b>",
            *self._score_lines(view.scores),
        ]
        if view.actor_status != "active":
            lines.append("\nВы зритель. Управлять партией уже нельзя.")
        if final:
            lines.extend(["", "Проверьте апелляции, затем хост завершит игру."])
        builder = InlineKeyboardBuilder()
        if view.actor_status == "active":
            builder.button(text="🏆 Посмотреть топ", callback_data=self._cb(view, "hat_main"))
            if view.appealable_cards:
                builder.button(text="⚖️ Апелляция", callback_data=self._cb(view, "hat_appeals", "0"))
        if view.is_host and view.actor_status == "active":
            builder.button(
                text="🏁 Завершить игру" if final else f"▶️ Начать раунд {next_round}",
                callback_data=self._cb(view, "hat_finish" if final else "hat_round"),
            )
            builder.button(text="⚠️ Аварийно завершить", callback_data=self._cb(view, "hat_cancel"))
        builder.adjust(1)
        return "\n".join(lines), builder.as_markup()

    def _render_appeal(self, view: HatRoomView) -> tuple[str, InlineKeyboardMarkup]:
        appeal = view.appeal
        if appeal is None:
            return self._render_break(view)
        lines = [
            "<b>Не засчитывать это слово?</b>",
            f"Карточка: <b>{_name(appeal.card_text)}</b>",
            f"Пара: {_name(appeal.explainer_name)} → {_name(appeal.guesser_name)}",
            f"Да: {appeal.yes_count} · Нет: {appeal.no_count}",
        ]
        builder = InlineKeyboardBuilder()
        if view.actor_member_id in appeal.eligible_member_ids and appeal.actor_choice is None:
            builder.button(text="Да", callback_data=self._cb(view, "hat_vote", f"{appeal.id}:1"))
            builder.button(text="Нет", callback_data=self._cb(view, "hat_vote", f"{appeal.id}:0"))
            builder.adjust(2)
        elif view.actor_member_id in appeal.eligible_member_ids:
            lines.append("Ваш голос учтён. Ожидайте остальных.")
        else:
            lines.append("Вы не голосуете в этой апелляции.")
        return "\n".join(lines), builder.as_markup()

    def _render_last_word_claim(self, view: HatRoomView) -> tuple[str, InlineKeyboardMarkup]:
        claim = view.last_word_claim
        if claim is None:
            return self._render_turn(view)
        lines = [
            "<b>Действительно успел?</b>",
            f"Игрок {_name(claim.explainer_name)} говорит, что успел объяснить:",
            f"<b>{_name(claim.card_text)}</b>",
            f"Да: {claim.yes_count} · Нет: {claim.no_count}",
        ]
        builder = InlineKeyboardBuilder()
        if view.actor_member_id in claim.automatic_yes_member_ids:
            lines.append("Голос вашей пары автоматически учтён: «Да».")
        elif view.actor_member_id in claim.eligible_member_ids and claim.actor_choice is None:
            builder.button(
                text="Да", callback_data=self._cb(view, "hat_last_word_vote", f"{claim.id}:1")
            )
            builder.button(
                text="Нет", callback_data=self._cb(view, "hat_last_word_vote", f"{claim.id}:0")
            )
            builder.adjust(2)
        elif view.actor_member_id in claim.eligible_member_ids:
            lines.append("Ваш голос учтён. Ожидайте остальных.")
        else:
            lines.append("Вы не голосуете за это слово.")
        return "\n".join(lines), builder.as_markup()

    def _render_finished(self, view: HatRoomView) -> tuple[str, InlineKeyboardMarkup]:
        title = (
            "Партия отменена хостом."
            if view.result == "cancelled"
            else "Партия завершена."
            if view.result != "insufficient_players"
            else "Партия завершена: осталось меньше трёх игроков."
        )
        lines = [
            "<b>🎩 Шляпа — партия завершена</b>",
            title,
            "",
            "<b>Итоговый топ</b>",
            *self._score_lines(view.scores),
        ]
        builder = InlineKeyboardBuilder()
        builder.button(text="🎮 К играм", callback_data="h:home")
        return "\n".join(lines), builder.as_markup()

    def _cb(self, view: HatRoomView, action: str, argument: str = "") -> str:
        return pack_room_callback(view.room_id, view.revision, action, argument)

    @staticmethod
    def _member_lines(members: tuple[MemberView, ...]) -> list[str]:
        lines = [f"• {_name(member.display_name)}" for member in members[:20]]
        if len(members) > 20:
            lines.append(f"… и ещё {len(members) - 20}")
        return lines or ["—"]

    @staticmethod
    def _seat_lines(view: HatRoomView) -> list[str]:
        return [f"{seat.seat_number}. {_name(seat.display_name)}" for seat in view.seats] or [
            "Пока пусто."
        ]

    @staticmethod
    def _score_lines(scores: tuple[HatScoreView, ...]) -> list[str]:
        lines: list[str] = []
        previous: int | None = None
        rank = 0
        for index, score in enumerate(scores, start=1):
            if score.score != previous:
                rank = index
                previous = score.score
            suffix = "" if score.is_active else " (вышел)"
            lines.append(f"{rank}. {_name(score.display_name)} — {score.score}{suffix}")
        return lines or ["—"]

    @staticmethod
    def _page(
        items: tuple[object, ...], requested: int, per_page: int = 8
    ) -> tuple[tuple[object, ...], int, int]:
        pages = max(1, ceil(len(items) / per_page))
        page = min(max(requested, 0), pages - 1)
        return items[page * per_page : (page + 1) * per_page], page, pages

    def _pagination(
        self, builder: InlineKeyboardBuilder, view: HatRoomView, action: str, page: int, pages: int
    ) -> None:
        if pages <= 1:
            return
        if page:
            builder.button(text="←", callback_data=self._cb(view, action, str(page - 1)))
        builder.button(text=f"{page + 1}/{pages}", callback_data=self._cb(view, "noop"))
        if page + 1 < pages:
            builder.button(text="→", callback_data=self._cb(view, action, str(page + 1)))
        builder.adjust(3)
