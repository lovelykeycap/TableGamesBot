from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import DomainError
from app.storage.database import Database
from app.storage.models import CatalogLocation, CatalogTheme


@dataclass(frozen=True)
class CatalogLocationView:
    id: int
    theme_id: int
    name: str
    is_active: bool


@dataclass(frozen=True)
class CatalogThemeView:
    id: int
    name: str
    is_active: bool
    locations: tuple[CatalogLocationView, ...]


class CatalogService:
    """Mutable catalog backed by an append-only, human-readable audit log."""

    def __init__(self, database: Database, audit_log_path: Path) -> None:
        self._database = database
        self._audit_log_path = audit_log_path

    async def list_themes(self) -> list[CatalogThemeView]:
        async with self._database.session() as session:
            themes = list(
                await session.scalars(
                    select(CatalogTheme).order_by(CatalogTheme.name.collate("NOCASE"))
                )
            )
            locations = list(
                await session.scalars(
                    select(CatalogLocation).order_by(
                        CatalogLocation.theme_id, CatalogLocation.name.collate("NOCASE")
                    )
                )
            )
        by_theme: dict[int, list[CatalogLocationView]] = {}
        for location in locations:
            by_theme.setdefault(location.theme_id, []).append(self._location_view(location))
        return [
            CatalogThemeView(
                id=theme.id,
                name=theme.name,
                is_active=theme.is_active,
                locations=tuple(by_theme.get(theme.id, [])),
            )
            for theme in themes
        ]

    async def create_theme(self, actor_id: int, name: str) -> int:
        name = self._validate_name(name, "Название темы")
        async with self._database.session() as session, session.begin():
            await self._ensure_theme_name_is_unique(session, name)
            theme = CatalogTheme(name=name, is_active=True)
            session.add(theme)
            await session.flush()
            after = self._theme_dict(theme)
        self._append_event(actor_id, "theme_created", before=None, after=after)
        return int(after["id"])

    async def rename_theme(self, actor_id: int, theme_id: int, name: str) -> None:
        name = self._validate_name(name, "Название темы")
        async with self._database.session() as session, session.begin():
            theme = await self._require_theme(session, theme_id)
            await self._ensure_theme_name_is_unique(session, name, except_id=theme.id)
            before = self._theme_dict(theme)
            theme.name = name
            await session.flush()
            after = self._theme_dict(theme)
        self._append_event(actor_id, "theme_renamed", before=before, after=after)

    async def set_theme_active(self, actor_id: int, theme_id: int, is_active: bool) -> None:
        async with self._database.session() as session, session.begin():
            theme = await self._require_theme(session, theme_id)
            before = self._theme_dict(theme)
            theme.is_active = is_active
            await session.flush()
            after = self._theme_dict(theme)
        self._append_event(actor_id, "theme_activation_changed", before=before, after=after)

    async def delete_theme(self, actor_id: int, theme_id: int) -> None:
        async with self._database.session() as session, session.begin():
            theme = await self._require_theme(session, theme_id)
            locations = list(
                await session.scalars(
                    select(CatalogLocation).where(CatalogLocation.theme_id == theme.id)
                )
            )
            before = {
                "theme": self._theme_dict(theme),
                "locations": [self._location_dict(item) for item in locations],
            }
            for location in locations:
                await session.delete(location)
            await session.delete(theme)
        self._append_event(actor_id, "theme_deleted", before=before, after=None)

    async def create_location(self, actor_id: int, theme_id: int, name: str) -> int:
        name = self._validate_name(name, "Название локации")
        async with self._database.session() as session, session.begin():
            await self._require_theme(session, theme_id)
            location = CatalogLocation(theme_id=theme_id, name=name, is_active=True)
            session.add(location)
            await session.flush()
            after = self._location_dict(location)
        self._append_event(actor_id, "location_created", before=None, after=after)
        return int(after["id"])

    async def rename_location(self, actor_id: int, location_id: int, name: str) -> None:
        name = self._validate_name(name, "Название локации")
        async with self._database.session() as session, session.begin():
            location = await self._require_location(session, location_id)
            before = self._location_dict(location)
            location.name = name
            await session.flush()
            after = self._location_dict(location)
        self._append_event(actor_id, "location_renamed", before=before, after=after)

    async def set_location_active(self, actor_id: int, location_id: int, is_active: bool) -> None:
        async with self._database.session() as session, session.begin():
            location = await self._require_location(session, location_id)
            before = self._location_dict(location)
            location.is_active = is_active
            await session.flush()
            after = self._location_dict(location)
        self._append_event(actor_id, "location_activation_changed", before=before, after=after)

    async def move_location(self, actor_id: int, location_id: int, theme_id: int) -> None:
        async with self._database.session() as session, session.begin():
            location = await self._require_location(session, location_id)
            await self._require_theme(session, theme_id)
            before = self._location_dict(location)
            location.theme_id = theme_id
            await session.flush()
            after = self._location_dict(location)
        self._append_event(actor_id, "location_moved", before=before, after=after)

    async def delete_location(self, actor_id: int, location_id: int) -> None:
        async with self._database.session() as session, session.begin():
            location = await self._require_location(session, location_id)
            before = self._location_dict(location)
            await session.delete(location)
        self._append_event(actor_id, "location_deleted", before=before, after=None)

    async def list_audit_events(self, limit: int = 20) -> list[dict[str, object]]:
        if not self._audit_log_path.exists():
            return []
        events: list[dict[str, object]] = []
        with self._audit_log_path.open("r", encoding="utf-8") as audit_file:
            for line in audit_file:
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    events.append(parsed)
        return list(reversed(events[-limit:]))

    async def restore_deleted_theme(self, actor_id: int, event_id: str) -> int:
        event = await self._event_by_id(event_id)
        if event.get("action") != "theme_deleted" or not isinstance(event.get("before"), dict):
            raise DomainError("В журнале нет удаления темы с таким идентификатором.")
        before = event["before"]
        theme_data = before.get("theme") if isinstance(before, dict) else None
        locations_data = before.get("locations") if isinstance(before, dict) else None
        if not isinstance(theme_data, dict) or not isinstance(locations_data, list):
            raise DomainError("Запись журнала неполная; восстановление невозможно.")
        theme_id = int(theme_data["id"])
        async with self._database.session() as session, session.begin():
            if await session.get(CatalogTheme, theme_id) is not None:
                raise DomainError("Эта тема уже существует.")
            theme = CatalogTheme(
                id=theme_id,
                name=str(theme_data["name"]),
                is_active=bool(theme_data["is_active"]),
            )
            session.add(theme)
            for location_data in locations_data:
                if not isinstance(location_data, dict):
                    continue
                location_id = int(location_data["id"])
                if await session.get(CatalogLocation, location_id) is not None:
                    raise DomainError("Нельзя восстановить тему: ID одной из локаций уже занят.")
                session.add(
                    CatalogLocation(
                        id=location_id,
                        theme_id=theme_id,
                        name=str(location_data["name"]),
                        is_active=bool(location_data["is_active"]),
                    )
                )
        self._append_event(actor_id, "theme_restored", before=None, after=before)
        return theme_id

    async def _event_by_id(self, event_id: str) -> dict[str, object]:
        if not self._audit_log_path.exists():
            raise DomainError("Журнал изменений пока пуст.")
        with self._audit_log_path.open("r", encoding="utf-8") as audit_file:
            for line in audit_file:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("id") == event_id:
                    return event
        raise DomainError("Событие журнала не найдено.")

    def _append_event(self, actor_id: int, action: str, *, before: object, after: object) -> None:
        event = {
            "id": secrets.token_hex(8),
            "at": datetime.now(timezone.utc).isoformat(),
            "actor_telegram_id": actor_id,
            "action": action,
            "before": before,
            "after": after,
        }
        self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._audit_log_path.open("a", encoding="utf-8") as audit_file:
            audit_file.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")

    @staticmethod
    def _validate_name(value: str, label: str) -> str:
        result = value.strip()
        if not result:
            raise DomainError(f"{label} не может быть пустым.")
        if len(result) > 256:
            raise DomainError(f"{label} должно быть не длиннее 256 символов.")
        return result

    async def _ensure_theme_name_is_unique(
        self, session: AsyncSession, name: str, except_id: int | None = None
    ) -> None:
        statement = select(CatalogTheme.id).where(func.lower(CatalogTheme.name) == name.lower())
        if except_id is not None:
            statement = statement.where(CatalogTheme.id != except_id)
        existing = await session.scalar(statement)
        if existing is not None:
            raise DomainError("Тема с таким названием уже существует.")

    async def _require_theme(self, session: AsyncSession, theme_id: int) -> CatalogTheme:
        theme = await session.get(CatalogTheme, theme_id)
        if theme is None:
            raise DomainError("Тема не найдена.")
        return theme

    async def _require_location(self, session: AsyncSession, location_id: int) -> CatalogLocation:
        location = await session.get(CatalogLocation, location_id)
        if location is None:
            raise DomainError("Локация не найдена.")
        return location

    @staticmethod
    def _theme_dict(theme: CatalogTheme) -> dict[str, object]:
        return {"id": theme.id, "name": theme.name, "is_active": theme.is_active}

    @staticmethod
    def _location_dict(location: CatalogLocation) -> dict[str, object]:
        return {
            "id": location.id,
            "theme_id": location.theme_id,
            "name": location.name,
            "is_active": location.is_active,
        }

    @staticmethod
    def _location_view(location: CatalogLocation) -> CatalogLocationView:
        return CatalogLocationView(
            id=location.id,
            theme_id=location.theme_id,
            name=location.name,
            is_active=location.is_active,
        )
