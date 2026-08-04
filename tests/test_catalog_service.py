from __future__ import annotations

from pathlib import Path

import pytest

from app.services.catalog_service import CatalogService
from app.storage.database import Database
from app.storage.models import Base


@pytest.mark.asyncio
async def test_deleted_theme_can_be_restored_from_jsonl_audit(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/catalog.db")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = CatalogService(database, tmp_path / "audit" / "catalog.jsonl")

    theme_id = await service.create_theme(100, "Учёба")
    await service.create_location(100, theme_id, "Школа")
    await service.delete_theme(100, theme_id)
    event = (await service.list_audit_events())[0]
    restored_id = await service.restore_deleted_theme(100, str(event["id"]))

    themes = await service.list_themes()
    assert restored_id == theme_id
    assert [(theme.name, [location.name for location in theme.locations]) for theme in themes] == [
        ("Учёба", ["Школа"])
    ]
    await database.dispose()
