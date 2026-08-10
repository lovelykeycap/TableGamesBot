from __future__ import annotations

from html import escape
from math import ceil

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.states import AdminInput
from app.domain.errors import DomainError
from app.services.catalog_service import CatalogService, CatalogThemeView
from app.services.delivery_service import DeliveryService
from app.services.room_service import RoomService


def create_admin_router(
    rooms: RoomService,
    catalog: CatalogService,
    delivery: DeliveryService,
    admin_ids: tuple[int, ...],
) -> Router:
    router = Router(name="admin")

    @router.message(Command("admin"))
    async def show_admin(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        await _ensure_user(rooms, message)
        await delivery.delete_processed_message(message)
        if not _is_admin(message.from_user.id, admin_ids):
            return
        playing_room_id = await rooms.playing_room_for_user(message.from_user.id)
        if playing_room_id is not None:
            await state.clear()
            await delivery.render_room_for_user(playing_room_id, message.from_user.id)
            return
        await state.clear()
        await _render_admin_home(catalog, delivery, message.from_user.id)

    @router.callback_query(F.data.startswith("a:"))
    async def admin_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.from_user is None or callback.data is None:
            return
        if not _is_admin(callback.from_user.id, admin_ids):
            await callback.answer("Недостаточно прав.")
            return
        playing_room_id = await rooms.playing_room_for_user(callback.from_user.id)
        if playing_room_id is not None:
            await state.clear()
            await delivery.render_room_for_user(playing_room_id, callback.from_user.id)
            await callback.answer("Админка недоступна, пока вы участвуете в партии.")
            return
        try:
            await _handle_admin_callback(
                catalog, delivery, callback.from_user.id, callback.data, state
            )
        except (DomainError, ValueError) as error:
            await callback.answer(str(error))
            return
        await callback.answer()

    @router.message(AdminInput.waiting_for_text, F.text, ~F.text.startswith("/"))
    async def receive_admin_text(message: Message, state: FSMContext) -> None:
        if message.from_user is None or not _is_admin(message.from_user.id, admin_ids):
            return
        await delivery.delete_processed_message(message)
        playing_room_id = await rooms.playing_room_for_user(message.from_user.id)
        if playing_room_id is not None:
            await state.clear()
            await delivery.render_room_for_user(playing_room_id, message.from_user.id)
            return
        data = await state.get_data()
        operation = data.get("operation")
        try:
            if operation == "theme_create":
                theme_id = await catalog.create_theme(message.from_user.id, message.text or "")
                await state.clear()
                await _render_theme(catalog, delivery, message.from_user.id, theme_id)
            elif operation == "theme_rename":
                theme_id = int(data["theme_id"])
                await catalog.rename_theme(message.from_user.id, theme_id, message.text or "")
                await state.clear()
                await _render_theme(catalog, delivery, message.from_user.id, theme_id)
            elif operation == "location_create":
                theme_id = int(data["theme_id"])
                await catalog.create_location(message.from_user.id, theme_id, message.text or "")
                await state.clear()
                await _render_theme(catalog, delivery, message.from_user.id, theme_id)
            elif operation == "location_rename":
                location_id = int(data["location_id"])
                theme_id = int(data["theme_id"])
                await catalog.rename_location(message.from_user.id, location_id, message.text or "")
                await state.clear()
                await _render_theme(catalog, delivery, message.from_user.id, theme_id)
            else:
                await state.clear()
                await _render_admin_home(catalog, delivery, message.from_user.id)
        except (DomainError, ValueError) as error:
            await delivery.render_custom_home(
                message.from_user.id,
                f"<b>Админка каталога</b>\n{escape(str(error))}\nОтправьте значение ещё раз.",
                _back_to_admin_markup(),
            )

    return router


async def _handle_admin_callback(
    catalog: CatalogService,
    delivery: DeliveryService,
    user_id: int,
    data: str,
    state: FSMContext,
) -> None:
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    if action == "home":
        await state.clear()
        await delivery.render_home(user_id)
    elif action == "admin":
        await state.clear()
        await _render_admin_home(catalog, delivery, user_id)
    elif action == "noop":
        return
    elif action == "themes":
        await state.clear()
        await _render_theme_list(
            catalog, delivery, user_id, int(parts[2] if len(parts) > 2 else "0")
        )
    elif action == "theme":
        await state.clear()
        await _render_theme(catalog, delivery, user_id, int(parts[2]))
    elif action == "add_theme":
        await state.set_state(AdminInput.waiting_for_text)
        await state.update_data(operation="theme_create")
        await _render_input_prompt(delivery, user_id, "Введите название новой темы.")
    elif action == "rename_theme":
        theme_id = int(parts[2])
        await state.set_state(AdminInput.waiting_for_text)
        await state.update_data(operation="theme_rename", theme_id=theme_id)
        await _render_input_prompt(delivery, user_id, "Введите новое название темы.")
    elif action == "theme_active":
        theme_id, raw = int(parts[2]), parts[3]
        await catalog.set_theme_active(user_id, theme_id, raw == "1")
        await _render_theme(catalog, delivery, user_id, theme_id)
    elif action == "confirm_delete_theme":
        await _render_delete_confirmation(delivery, user_id, "тему", f"a:delete_theme:{parts[2]}")
    elif action == "delete_theme":
        await catalog.delete_theme(user_id, int(parts[2]))
        await _render_theme_list(catalog, delivery, user_id, 0)
    elif action == "add_location":
        theme_id = int(parts[2])
        await state.set_state(AdminInput.waiting_for_text)
        await state.update_data(operation="location_create", theme_id=theme_id)
        await _render_input_prompt(delivery, user_id, "Введите название новой локации.")
    elif action == "location":
        await state.clear()
        await _render_location(catalog, delivery, user_id, int(parts[2]), int(parts[3]))
    elif action == "rename_location":
        location_id, theme_id = int(parts[2]), int(parts[3])
        await state.set_state(AdminInput.waiting_for_text)
        await state.update_data(
            operation="location_rename", location_id=location_id, theme_id=theme_id
        )
        await _render_input_prompt(delivery, user_id, "Введите новое название локации.")
    elif action == "location_active":
        location_id, theme_id, raw = int(parts[2]), int(parts[3]), parts[4]
        await catalog.set_location_active(user_id, location_id, raw == "1")
        await _render_location(catalog, delivery, user_id, location_id, theme_id)
    elif action == "confirm_delete_location":
        await _render_delete_confirmation(
            delivery, user_id, "локацию", f"a:delete_location:{parts[2]}:{parts[3]}"
        )
    elif action == "delete_location":
        location_id, theme_id = int(parts[2]), int(parts[3])
        await catalog.delete_location(user_id, location_id)
        await _render_theme(catalog, delivery, user_id, theme_id)
    elif action == "move_location":
        await _render_move_selector(catalog, delivery, user_id, int(parts[2]), int(parts[3]))
    elif action == "move_to":
        location_id, target_theme_id = int(parts[2]), int(parts[3])
        await catalog.move_location(user_id, location_id, target_theme_id)
        await _render_theme(catalog, delivery, user_id, target_theme_id)
    elif action == "history":
        await _render_history(catalog, delivery, user_id)
    elif action == "restore":
        theme_id = await catalog.restore_deleted_theme(user_id, parts[2])
        await _render_theme(catalog, delivery, user_id, theme_id)
    else:
        raise DomainError("Неизвестное действие админки.")


async def _render_admin_home(
    catalog: CatalogService, delivery: DeliveryService, user_id: int
) -> None:
    themes = await catalog.list_themes()
    text = f"<b>Админка каталога</b>\nТем в каталоге: {len(themes)}"
    builder = InlineKeyboardBuilder()
    builder.button(text="🗂 Темы и локации", callback_data="a:themes:0")
    builder.button(text="🧾 История изменений", callback_data="a:history")
    builder.button(text="← К играм", callback_data="a:home")
    builder.adjust(1)
    await delivery.render_custom_home(user_id, text, builder.as_markup())


async def _render_theme_list(
    catalog: CatalogService, delivery: DeliveryService, user_id: int, requested_page: int
) -> None:
    themes = await catalog.list_themes()
    page_items, page, pages = _page(themes, requested_page)
    text = "<b>Темы каталога</b>\nВыберите тему для редактирования."
    builder = InlineKeyboardBuilder()
    for theme in page_items:
        status = "✅" if theme.is_active else "⛔"
        builder.button(
            text=f"{status} {theme.name} ({len(theme.locations)})",
            callback_data=f"a:theme:{theme.id}",
        )
    builder.button(text="➕ Создать тему", callback_data="a:add_theme")
    builder.adjust(1)
    _pagination(builder, "a:themes", page, pages)
    builder.button(text="← В админку", callback_data="a:admin")
    builder.adjust(1)
    await delivery.render_custom_home(user_id, text, builder.as_markup())


async def _render_theme(
    catalog: CatalogService, delivery: DeliveryService, user_id: int, theme_id: int
) -> None:
    theme = next((item for item in await catalog.list_themes() if item.id == theme_id), None)
    if theme is None:
        raise DomainError("Тема не найдена.")
    status = "активна" if theme.is_active else "выключена"
    text = f"<b>Тема: {escape(theme.name)}</b>\nСтатус: {status}"
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Переименовать", callback_data=f"a:rename_theme:{theme.id}")
    builder.button(
        text="⛔ Выключить" if theme.is_active else "✅ Включить",
        callback_data=f"a:theme_active:{theme.id}:{0 if theme.is_active else 1}",
    )
    builder.button(text="➕ Добавить локацию", callback_data=f"a:add_location:{theme.id}")
    builder.adjust(1)
    for location in theme.locations:
        status = "✅" if location.is_active else "⛔"
        builder.button(
            text=f"{status} {location.name}", callback_data=f"a:location:{location.id}:{theme.id}"
        )
    builder.adjust(1)
    builder.button(text="🗑 Удалить тему", callback_data=f"a:confirm_delete_theme:{theme.id}")
    builder.button(text="← К темам", callback_data="a:themes:0")
    builder.adjust(1)
    await delivery.render_custom_home(user_id, text, builder.as_markup())


async def _render_location(
    catalog: CatalogService,
    delivery: DeliveryService,
    user_id: int,
    location_id: int,
    theme_id: int,
) -> None:
    themes = await catalog.list_themes()
    location = next(
        (
            location
            for theme in themes
            for location in theme.locations
            if location.id == location_id
        ),
        None,
    )
    if location is None:
        raise DomainError("Локация не найдена.")
    status = "активна" if location.is_active else "выключена"
    text = f"<b>Локация: {escape(location.name)}</b>\nСтатус: {status}"
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✏️ Переименовать", callback_data=f"a:rename_location:{location.id}:{theme_id}"
    )
    builder.button(
        text="⛔ Выключить" if location.is_active else "✅ Включить",
        callback_data=(
            f"a:location_active:{location.id}:{theme_id}:{0 if location.is_active else 1}"
        ),
    )
    builder.button(
        text="↪️ Перенести в другую тему", callback_data=f"a:move_location:{location.id}:{theme_id}"
    )
    builder.button(
        text="🗑 Удалить локацию",
        callback_data=f"a:confirm_delete_location:{location.id}:{theme_id}",
    )
    builder.button(text="← К теме", callback_data=f"a:theme:{theme_id}")
    builder.adjust(1)
    await delivery.render_custom_home(user_id, text, builder.as_markup())


async def _render_move_selector(
    catalog: CatalogService,
    delivery: DeliveryService,
    user_id: int,
    location_id: int,
    current_theme_id: int,
) -> None:
    themes = await catalog.list_themes()
    text = "<b>Перенос локации</b>\nВыберите новую тему."
    builder = InlineKeyboardBuilder()
    for theme in themes:
        if theme.id != current_theme_id:
            builder.button(text=theme.name, callback_data=f"a:move_to:{location_id}:{theme.id}")
    builder.button(text="← Назад", callback_data=f"a:location:{location_id}:{current_theme_id}")
    builder.adjust(1)
    await delivery.render_custom_home(user_id, text, builder.as_markup())


async def _render_history(catalog: CatalogService, delivery: DeliveryService, user_id: int) -> None:
    events = await catalog.list_audit_events(12)
    lines = ["<b>История изменений</b>"]
    builder = InlineKeyboardBuilder()
    if not events:
        lines.append("Изменений пока нет.")
    for event in events:
        event_id = str(event.get("id", ""))
        action = escape(str(event.get("action", "unknown")))
        lines.append(f"• <code>{event_id}</code> — {action}")
        if event.get("action") == "theme_deleted":
            builder.button(text=f"Восстановить {event_id}", callback_data=f"a:restore:{event_id}")
    builder.adjust(1)
    builder.button(text="← В админку", callback_data="a:admin")
    builder.adjust(1)
    await delivery.render_custom_home(user_id, "\n".join(lines), builder.as_markup())


async def _render_input_prompt(delivery: DeliveryService, user_id: int, text: str) -> None:
    await delivery.render_custom_home(
        user_id, f"<b>Админка каталога</b>\n{escape(text)}", _back_to_admin_markup()
    )


async def _render_delete_confirmation(
    delivery: DeliveryService, user_id: int, noun: str, callback_data: str
) -> None:
    builder = InlineKeyboardBuilder()
    builder.button(text="Да, удалить", callback_data=callback_data)
    builder.button(text="Отмена", callback_data="a:themes:0")
    builder.adjust(1)
    await delivery.render_custom_home(
        user_id,
        f"<b>Подтвердите удаление</b>\nУдалить {noun} и связанные данные?",
        builder.as_markup(),
    )


def _back_to_admin_markup() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="← В админку", callback_data="a:admin")
    return builder.as_markup()


def _page(
    items: list[CatalogThemeView], requested_page: int, per_page: int = 8
) -> tuple[list[CatalogThemeView], int, int]:
    pages = max(1, ceil(len(items) / per_page))
    page = min(max(requested_page, 0), pages - 1)
    start = page * per_page
    return items[start : start + per_page], page, pages


def _pagination(builder: InlineKeyboardBuilder, prefix: str, page: int, pages: int) -> None:
    if pages <= 1:
        return
    if page > 0:
        builder.button(text="←", callback_data=f"{prefix}:{page - 1}")
    builder.button(text=f"{page + 1}/{pages}", callback_data="a:noop")
    if page + 1 < pages:
        builder.button(text="→", callback_data=f"{prefix}:{page + 1}")
    builder.adjust(3)


def _is_admin(user_id: int, admin_ids: tuple[int, ...]) -> bool:
    return user_id in admin_ids


async def _ensure_user(rooms: RoomService, message: Message) -> None:
    assert message.from_user is not None
    user = message.from_user
    display_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    await rooms.ensure_user(user.id, display_name or str(user.id), user.username)
