from __future__ import annotations

from html import escape
from math import ceil

from aiogram.types import CopyTextButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import pack_room_callback
from app.domain.games.spy import ROLE_SPY, VOTE_EXILE, VOTE_REVEAL
from app.services.views import MemberView, RoomView


def _name(value: str) -> str:
    return escape(value[:80])


def _result_label(result: str | None) -> str:
    return {
        "spies_win_reveal": "🏆 Победа шпионов: вскрытие подтверждено.",
        "civilians_win": "🏆 Победа мирных: все шпионы выбыли.",
        "cancelled": "⚠️ Партия аварийно отменена хостом.",
    }.get(result, "Партия завершена.")


class RoomRenderer:
    def __init__(self, bot_username: str) -> None:
        self._bot_username = bot_username

    def render(self, view: RoomView) -> tuple[str, InlineKeyboardMarkup]:
        if view.status == "lobby":
            return self._render_lobby(view)
        if view.status in {"playing", "finished"}:
            return self._render_game(view)
        return self._render_closed(view)

    def render_theme_selector(self, view: RoomView, page: int) -> tuple[str, InlineKeyboardMarkup]:
        page_items, page, pages = self._page(view.themes, page)
        lines = ["<b>Темы этой комнаты</b>", "✅ — будет участвовать в выборе, ⛔ — выключена."]
        if not page_items:
            lines.append("В глобальном каталоге пока нет активных тем.")
        builder = InlineKeyboardBuilder()
        for theme in page_items:
            status = "✅" if theme.is_selected else "⛔"
            unavailable = " · нет локаций" if not theme.has_active_locations else ""
            builder.button(
                text=f"{status} {theme.name}{unavailable}",
                callback_data=self._cb(view, "theme", str(theme.id)),
            )
        builder.adjust(1)
        self._pagination(builder, view, "themes", page, pages)
        builder.button(text="← К комнате", callback_data=self._cb(view, "main"))
        builder.adjust(1)
        return "\n".join(lines), builder.as_markup()

    def render_candidate_selector(
        self, view: RoomView, page: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        candidates = tuple(
            member for member in view.active_members if member.id != view.actor_member_id
        )
        page_items, page, pages = self._page(candidates, page)
        lines = ["<b>Выдвинуть на голосование</b>", "Выберите кандидата:"]
        builder = InlineKeyboardBuilder()
        for member in page_items:
            builder.button(
                text=_name(member.display_name),
                callback_data=self._cb(view, "candidate", str(member.id)),
            )
        builder.adjust(1)
        self._pagination(builder, view, "candidates", page, pages)
        builder.button(text="← Назад", callback_data=self._cb(view, "main"))
        builder.adjust(1)
        return "\n".join(lines), builder.as_markup()

    def render_kick_selector(self, view: RoomView, page: int) -> tuple[str, InlineKeyboardMarkup]:
        candidates = tuple(
            member for member in view.active_members if member.user_id != view.actor_user_id
        )
        page_items, page, pages = self._page(candidates, page)
        lines = ["<b>Исключить из лобби</b>", "Игрок сможет войти снова, пока вход открыт."]
        builder = InlineKeyboardBuilder()
        for member in page_items:
            builder.button(
                text=f"Исключить: {_name(member.display_name)}",
                callback_data=self._cb(view, "kick", str(member.id)),
            )
        builder.adjust(1)
        self._pagination(builder, view, "kicks", page, pages)
        builder.button(text="← Назад", callback_data=self._cb(view, "main"))
        builder.adjust(1)
        return "\n".join(lines), builder.as_markup()

    def _render_lobby(self, view: RoomView) -> tuple[str, InlineKeyboardMarkup]:
        active = view.active_members
        settings = view.settings
        mode = str(settings.get("spy_count_mode", "random"))
        if mode == "random":
            spy_setting = "случайно от 1 до числа игроков"
        else:
            spy_setting = f"точно {int(settings.get('spy_count', 1))}"
        selected_count = sum(theme.is_selected for theme in view.themes)
        link = f"https://t.me/{self._bot_username}?start=join_{view.join_token}"
        lines = [
            "<b>■ Шпион — лобби</b>",
            f"Код комнаты: <code>{view.join_code}</code>",
            "",
            f"Игроки ({len(active)}/40):",
            *self._member_lines(active),
            "",
            f"Шпионы: {spy_setting}",
            f"Шпионы знают друг друга: {'да' if settings.get('spies_know_each_other') else 'нет'}",
            f"Темы комнаты: {selected_count} из {len(view.themes)}",
            f"Вход: {'открыт' if view.is_join_open else 'закрыт'}",
        ]
        if view.is_host:
            lines.append("\nВы хост комнаты.")
        builder = InlineKeyboardBuilder()
        builder.button(
            text="📋 Скопировать код",
            copy_text=CopyTextButton(text=view.join_code),
        )
        builder.button(
            text="🔗 Скопировать ссылку",
            copy_text=CopyTextButton(text=link),
        )
        builder.adjust(2)
        if view.is_host:
            builder.button(
                text="🔒 Закрыть вход" if view.is_join_open else "🔓 Открыть вход",
                callback_data=self._cb(view, "open", "0" if view.is_join_open else "1"),
            )
            builder.button(
                text=f"{'✅ ' if mode == 'random' else ''}🎲 Случайное число",
                callback_data=self._cb(view, "mode", "random"),
            )
            builder.button(
                text=f"{'✅ ' if mode == 'exact' else ''}🔢 Точное число",
                callback_data=self._cb(view, "mode", "exact"),
            )
            builder.adjust(1, 2)
            if mode == "exact":
                count = int(settings.get("spy_count", 1))
                builder.button(text="−", callback_data=self._cb(view, "spy_delta", "-1"))
                builder.button(text=f"Шпионов: {count}", callback_data=self._cb(view, "noop"))
                builder.button(text="+", callback_data=self._cb(view, "spy_delta", "1"))
                builder.adjust(3)
            know_text = (
                "👁 Шпионы видят друг друга: да"
                if settings.get("spies_know_each_other")
                else "🙈 Шпионы видят друг друга: нет"
            )
            builder.button(text=know_text, callback_data=self._cb(view, "know"))
            builder.button(text="🗂 Выбрать темы", callback_data=self._cb(view, "themes", "0"))
            builder.adjust(1, 1)
            builder.button(text="👤 Исключить игрока", callback_data=self._cb(view, "kicks", "0"))
            builder.button(text="▶️ Начать игру", callback_data=self._cb(view, "start"))
            builder.adjust(1, 1)
        builder.button(text="🚪 Выйти из комнаты", callback_data=self._cb(view, "leave"))
        builder.adjust(1)
        return "\n".join(lines), builder.as_markup()

    def _render_game(self, view: RoomView) -> tuple[str, InlineKeyboardMarkup]:
        game = view.game
        if game is None:
            return "<b>Ошибка партии</b>", InlineKeyboardBuilder().as_markup()
        builder = InlineKeyboardBuilder()
        if view.status == "finished":
            spies = tuple(member for member in view.members if member.role == ROLE_SPY)
            lines = [
                "<b>■ Шпион — партия завершена</b>",
                _result_label(game.result),
                f"Тема: <b>{escape(game.theme_name)}</b>",
                f"Локация: <b>{escape(game.location_name)}</b>",
                "Шпионы:",
                *self._member_lines(spies),
            ]
            builder.button(text="🎮 К играм", callback_data="h:home")
            builder.adjust(1)
            return "\n".join(lines), builder.as_markup()

        lines = ["<b>■ Шпион — игра идёт</b>"]
        if view.actor_status != "active":
            lines.append("Вы зритель. Управлять игрой уже нельзя.")
        elif view.actor_role == ROLE_SPY:
            lines.extend(["Ваша роль: <b>■ Шпион</b>", f"Тема: <b>{escape(game.theme_name)}</b>"])
            if game.spies_know_each_other:
                known = (
                    ", ".join(_name(name) for name in view.known_spy_names) or "других шпионов нет"
                )
                lines.append(f"Другие шпионы: {known}")
        else:
            lines.extend(
                [
                    "Ваша роль: <b>🙂 Мирный</b>",
                    f"Тема: <b>{escape(game.theme_name)}</b>",
                    f"Локация: <b>{escape(game.location_name)}</b>",
                ]
            )
        lines.extend(
            [f"В игре ({len(view.active_members)}):", *self._member_lines(view.active_members)]
        )
        if view.vote is not None:
            subject = next(
                (member for member in view.members if member.id == view.vote.subject_member_id),
                None,
            )
            subject_name = _name(subject.display_name) if subject else "игрок"
            title = "Изгнать" if view.vote.kind == VOTE_EXILE else "Подтвердить вскрытие"
            voted = view.vote.yes_count + view.vote.no_count
            lines.extend(
                [
                    f"\n<b>{title}: {subject_name}</b>",
                    f"Проголосовало: {voted}/{len(view.vote.eligible_member_ids)}",
                ]
            )
            if (
                view.actor_status == "active"
                and view.actor_member_id in view.vote.eligible_member_ids
            ):
                if view.vote.actor_choice is None:
                    yes_text, no_text = (
                        ("✅ Угадал", "❌ Не угадал")
                        if view.vote.kind == VOTE_REVEAL
                        else ("✅ За", "❌ Против")
                    )
                    builder.button(
                        text=yes_text, callback_data=self._cb(view, "vote", f"{view.vote.id}:1")
                    )
                    builder.button(
                        text=no_text, callback_data=self._cb(view, "vote", f"{view.vote.id}:0")
                    )
                    builder.adjust(2)
                else:
                    lines.append("Ваш голос учтён. Ожидайте остальных.")
        elif view.actor_status == "active":
            builder.button(
                text="🗳 Выдвинуть на голосование", callback_data=self._cb(view, "candidates", "0")
            )
            if view.actor_role == ROLE_SPY:
                builder.button(text="■ Вскрыться", callback_data=self._cb(view, "reveal"))
            builder.adjust(1, 1)
        if view.is_host and view.actor_status == "active":
            builder.button(text="⚠️ Аварийно завершить", callback_data=self._cb(view, "cancel"))
            builder.adjust(1)
        if view.actor_status == "active":
            builder.button(text="🚪 Выйти (роль раскроется)", callback_data=self._cb(view, "leave"))
            builder.adjust(1)
        return "\n".join(lines), builder.as_markup()

    def _render_closed(self, view: RoomView) -> tuple[str, InlineKeyboardMarkup]:
        builder = InlineKeyboardBuilder()
        builder.button(text="🎮 К играм", callback_data="h:home")
        return "<b>Комната закрыта.</b>", builder.as_markup()

    def _cb(self, view: RoomView, action: str, argument: str = "") -> str:
        return pack_room_callback(view.room_id, view.revision, action, argument)

    @staticmethod
    def _member_lines(members: tuple[MemberView, ...] | list[MemberView]) -> list[str]:
        if not members:
            return ["—"]
        visible = members[:20]
        lines = [f"• {_name(member.display_name)}" for member in visible]
        if remaining := len(members) - len(visible):
            lines.append(f"… и ещё {remaining}")
        return lines

    @staticmethod
    def _page(
        items: tuple[object, ...], requested_page: int, per_page: int = 8
    ) -> tuple[tuple[object, ...], int, int]:
        pages = max(1, ceil(len(items) / per_page))
        page = min(max(requested_page, 0), pages - 1)
        start = page * per_page
        return items[start : start + per_page], page, pages

    def _pagination(
        self, builder: InlineKeyboardBuilder, view: RoomView, action: str, page: int, pages: int
    ) -> None:
        if pages <= 1:
            return
        if page > 0:
            builder.button(text="←", callback_data=self._cb(view, action, str(page - 1)))
        builder.button(text=f"{page + 1}/{pages}", callback_data=self._cb(view, "noop"))
        if page + 1 < pages:
            builder.button(text="→", callback_data=self._cb(view, action, str(page + 1)))
        builder.adjust(3)


def home_markup() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="■ Создать комнату «Шпион»", callback_data="h:new:spy")
    builder.button(text="🎩 Создать комнату «Шляпа»", callback_data="h:new:hat")
    builder.button(text="🔑 Войти по коду", callback_data="h:join")
    builder.button(text="🎮 Активные комнаты", callback_data="h:rooms")
    builder.adjust(1)
    return builder.as_markup()


def home_text() -> str:
    return "<b>Настольные игры</b>\nСоздайте комнату или войдите по её коду."
