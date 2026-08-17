from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

from .db import ShopDatabase
from .models import CartLine, Product, format_money


@dataclass(slots=True)
class CheckoutState:
    step: str = "name"
    values: dict[str, str] = field(default_factory=dict)


def product_text(product: Product, currency: str) -> str:
    stock = f"В наличии: {product.stock}"
    return (
        f"<b>{html.escape(product.name)}</b>\n\n"
        f"{html.escape(product.description)}\n\n"
        f"Цена: <b>{format_money(product.price_kopecks, currency)}</b>\n{stock}"
    )


def cart_text(lines: list[CartLine], currency: str) -> str:
    if not lines:
        return "🧺 Ваша корзина пока пуста. Откройте каталог и выберите изделия."
    rows = ["<b>Ваша корзина</b>", ""]
    total = 0
    for line in lines:
        rows.append(
            f"• {html.escape(line.product.name)} — {line.quantity} шт. × "
            f"{format_money(line.product.price_kopecks, currency)} = "
            f"<b>{format_money(line.subtotal_kopecks, currency)}</b>"
        )
        total += line.subtotal_kopecks
    rows.extend(("", f"Итого: <b>{format_money(total, currency)}</b>"))
    return "\n".join(rows)


def order_notification(order_id: int, platform: str, customer_id: str, name: str, phone: str, address: str, comment: str, lines: list[CartLine], currency: str) -> str:
    items = "\n".join(
        f"• {html.escape(line.product.name)} — {line.quantity} шт. × {format_money(line.product.price_kopecks, currency)}"
        for line in lines
    )
    total = sum(line.subtotal_kopecks for line in lines)
    return (
        f"🛍 <b>Новый заказ №{order_id}</b>\n"
        f"Площадка: {html.escape(platform)}\n"
        f"ID покупателя: <code>{html.escape(customer_id)}</code>\n\n"
        f"{items}\n\nИтого: <b>{format_money(total, currency)}</b>\n\n"
        f"Имя: {html.escape(name)}\n"
        f"Телефон: {html.escape(phone)}\n"
        f"Доставка: {html.escape(address)}\n"
        f"Комментарий: {html.escape(comment or '—')}"
    )


def validate_phone(value: str) -> str:
    value = value.strip()
    digits = re.sub(r"\D", "", value)
    if not 10 <= len(digits) <= 15:
        raise ValueError("Введите телефон с кодом страны, например +7 900 123-45-67")
    return value


def existing_image(product: Product) -> Path | None:
    if not product.image_path:
        return None
    path = Path(product.image_path)
    return path if path.is_file() else None


def checkout_summary(db: ShopDatabase, platform: str, customer_id: str, values: dict[str, str], currency: str) -> str:
    lines = db.get_cart(platform, customer_id)
    return (
        f"{cart_text(lines, currency)}\n\n"
        f"Получатель: {html.escape(values['name'])}\n"
        f"Телефон: {html.escape(values['phone'])}\n"
        f"Доставка: {html.escape(values['address'])}\n"
        f"Комментарий: {html.escape(values.get('comment') or '—')}\n\n"
        "Всё верно?"
    )
