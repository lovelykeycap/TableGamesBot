from __future__ import annotations

from pathlib import Path

import pytest_asyncio

from app.services.locks import RoomLocks
from app.services.room_service import RoomService
from app.storage.database import Database
from app.storage.models import Base, CatalogLocation, CatalogTheme


@pytest_asyncio.fixture
async def room_service(tmp_path: Path) -> RoomService:
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = RoomService(database, RoomLocks())
    for user_id in range(1, 5):
        await service.ensure_user(user_id, f"Игрок {user_id}", None)
    async with database.session() as session, session.begin():
        theme = CatalogTheme(name="Учёба", is_active=True)
        session.add(theme)
        await session.flush()
        session.add(CatalogLocation(theme_id=theme.id, name="Школа", is_active=True))
    yield service
    await database.dispose()
