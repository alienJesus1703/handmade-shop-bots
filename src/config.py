from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_shop_token: str
    telegram_admin_token: str
    admin_telegram_ids: frozenset[int]
    max_bot_token: str | None
    max_webhook_url: str | None
    max_webhook_secret: str | None
    webhook_port: int
    shop_name: str
    currency: str
    shop_contact: str
    db_path: Path
    images_dir: Path
    log_level: str


def load_settings() -> Settings:
    load_dotenv()
    shop_token = os.getenv("TELEGRAM_SHOP_TOKEN", "").strip()
    admin_token = os.getenv("TELEGRAM_ADMIN_TOKEN", "").strip()
    raw_admin_ids = os.getenv("ADMIN_TELEGRAM_IDS", "").strip()
    missing = [
        name
        for name, value in (
            ("TELEGRAM_SHOP_TOKEN", shop_token),
            ("TELEGRAM_ADMIN_TOKEN", admin_token),
            ("ADMIN_TELEGRAM_IDS", raw_admin_ids),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Не заполнены обязательные переменные: {', '.join(missing)}")

    try:
        admin_ids = frozenset(int(value.strip()) for value in raw_admin_ids.split(",") if value.strip())
    except ValueError as exc:
        raise ValueError("ADMIN_TELEGRAM_IDS должен содержать числовые ID через запятую") from exc
    if not admin_ids:
        raise ValueError("ADMIN_TELEGRAM_IDS не может быть пустым")

    db_path = Path(os.getenv("DB_PATH", "data/shop.db"))
    images_dir = Path(os.getenv("PRODUCT_IMAGES_DIR", "data/products"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        telegram_shop_token=shop_token,
        telegram_admin_token=admin_token,
        admin_telegram_ids=admin_ids,
        max_bot_token=os.getenv("MAX_BOT_TOKEN", "").strip() or None,
        max_webhook_url=os.getenv("MAX_WEBHOOK_URL", "").strip() or None,
        max_webhook_secret=os.getenv("MAX_WEBHOOK_SECRET", "").strip() or None,
        webhook_port=int(os.getenv("WEBHOOK_PORT", "8080")),
        shop_name=os.getenv("SHOP_NAME", "Магазин ручной работы").strip(),
        currency=os.getenv("SHOP_CURRENCY", "₽").strip(),
        shop_contact=os.getenv("SHOP_CONTACT", "").strip(),
        db_path=db_path,
        images_dir=images_dir,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
