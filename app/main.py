from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.routers.admin import create_admin_router
from app.bot.routers.main import create_main_router
from app.config import get_settings
from app.services.catalog_service import CatalogService
from app.services.delivery_service import DeliveryService
from app.services.hat_scheduler import HatScheduler
from app.services.hat_service import HatService
from app.services.locks import RoomLocks
from app.services.room_service import RoomService
from app.storage.database import Database


async def run() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    database = Database(settings.database_url)
    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    locks = RoomLocks()
    rooms = RoomService(database, locks)
    hats = HatService(rooms)
    catalog = CatalogService(database, settings.audit_log_path)
    me = await bot.get_me()
    if not me.username:
        raise RuntimeError("The Telegram bot must have a username so room deep-links can work.")
    delivery = DeliveryService(bot, database, rooms, hats, me.username)
    hat_scheduler = HatScheduler(hats, delivery)
    dispatcher.include_router(create_admin_router(rooms, catalog, delivery, settings.admin_ids))
    dispatcher.include_router(create_main_router(rooms, hats, delivery))
    hat_scheduler.start()
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await hat_scheduler.stop()
        await bot.session.close()
        await database.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
