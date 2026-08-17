from __future__ import annotations

import asyncio
import logging
import sys

import aiohttp

from .admin_bot import TelegramAdminBot
from .config import load_settings
from .db import ShopDatabase
from .max_api import MaxAPI
from .max_shop import MaxShopBot
from .telegram_api import TelegramAPI
from .telegram_shop import TelegramShopBot


async def run() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db = ShopDatabase(settings.db_path)
    timeout = aiohttp.ClientTimeout(total=45, connect=15, sock_read=40)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        shop_api = TelegramAPI(settings.telegram_shop_token, session)
        admin_api = TelegramAPI(settings.telegram_admin_token, session)
        admin_bot = TelegramAdminBot(admin_api, db, settings)
        shop_bot = TelegramShopBot(shop_api, db, settings, admin_bot.notify_admins)
        tasks = [
            asyncio.create_task(shop_api.poll(shop_bot.handle), name="telegram-shop"),
            asyncio.create_task(admin_api.poll(admin_bot.handle), name="telegram-admin"),
        ]
        if settings.max_bot_token:
            max_api = MaxAPI(settings.max_bot_token, session)
            max_bot = MaxShopBot(max_api, db, settings, admin_bot.notify_admins)
            if settings.max_webhook_url:
                if not settings.max_webhook_secret:
                    raise ValueError("Для MAX_WEBHOOK_URL необходимо заполнить MAX_WEBHOOK_SECRET")
                tasks.append(asyncio.create_task(
                    max_api.serve_webhook(
                        max_bot.handle, settings.max_webhook_url, settings.max_webhook_secret, settings.webhook_port
                    ),
                    name="max-webhook",
                ))
            else:
                tasks.append(asyncio.create_task(max_api.poll(max_bot.handle), name="max-polling"))
        else:
            logging.getLogger(__name__).warning("MAX_BOT_TOKEN пуст: MAX-бот не запущен")
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            db.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except ValueError as exc:
        print(f"Ошибка настройки: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
