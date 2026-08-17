from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .config import Settings
from .db import ShopDatabase
from .max_api import MaxAPI
from .shop_common import (
    CheckoutState,
    cart_text,
    checkout_summary,
    existing_image,
    order_notification,
    product_text,
    validate_phone,
)

logger = logging.getLogger(__name__)
Notifier = Callable[[str], Awaitable[None]]


class MaxShopBot:
    PLATFORM = "max"

    def __init__(self, api: MaxAPI, db: ShopDatabase, settings: Settings, notify_admins: Notifier) -> None:
        self.api = api
        self.db = db
        self.settings = settings
        self.notify_admins = notify_admins
        self.checkouts: dict[int, CheckoutState] = {}

    @staticmethod
    def _user_id(update: dict[str, Any]) -> int | None:
        candidates = (
            update.get("user"),
            update.get("message", {}).get("sender"),
            update.get("callback", {}).get("user"),
            update.get("callback", {}).get("message", {}).get("sender"),
        )
        for user in candidates:
            if isinstance(user, dict) and user.get("user_id") is not None:
                return int(user["user_id"])
        return None

    @staticmethod
    def _menu_buttons() -> list[list[tuple[str, str]]]:
        return [[("🛍 Каталог", "catalog"), ("🧺 Корзина", "cart")], [("ℹ️ О магазине", "about")]]

    async def handle(self, update: dict[str, Any]) -> None:
        user_id = self._user_id(update)
        if user_id is None:
            logger.warning("MAX update без user_id: %s", update.get("update_type"))
            return
        update_type = update.get("update_type")
        if update_type == "message_callback":
            await self._callback(user_id, update.get("callback", {}))
        elif update_type in {"message_created", "bot_started"}:
            body = update.get("message", {}).get("body") or {}
            text = (body.get("text") or "").strip()
            await self._message(user_id, text, started=update_type == "bot_started")

    async def _message(self, user_id: int, text: str, *, started: bool = False) -> None:
        lowered = text.lower()
        if started or lowered in {"/start", "/menu", "меню"}:
            self.checkouts.pop(user_id, None)
            await self.api.send_message(
                user_id,
                f"Добро пожаловать в <b>{html.escape(self.settings.shop_name)}</b>! Здесь можно выбрать изделие и оформить заказ.",
                self._menu_buttons(),
            )
        elif lowered in {"/catalog", "каталог"}:
            await self.show_catalog(user_id)
        elif lowered in {"/cart", "корзина"}:
            await self.show_cart(user_id)
        elif lowered == "/cancel":
            self.checkouts.pop(user_id, None)
            await self.api.send_message(user_id, "Оформление отменено. Корзина сохранена.", self._menu_buttons())
        elif user_id in self.checkouts:
            await self._checkout_input(user_id, text)
        else:
            await self.api.send_message(user_id, "Выберите действие 👇", self._menu_buttons())

    async def show_catalog(self, user_id: int) -> None:
        products = self.db.list_products()
        if not products:
            await self.api.send_message(user_id, "Сейчас все изделия распроданы. Загляните немного позже 🌿", self._menu_buttons())
            return
        await self.api.send_message(user_id, f"<b>Каталог — {html.escape(self.settings.shop_name)}</b>")
        for product in products:
            await asyncio.sleep(0.55)  # documented MAX limit: 2 messages/second per dialog
            await self.api.send_message(
                user_id,
                product_text(product, self.settings.currency),
                [[("🧺 Добавить в корзину", f"add:{product.id}")]],
                existing_image(product),
            )

    async def show_cart(self, user_id: int) -> None:
        lines = self.db.get_cart(self.PLATFORM, str(user_id))
        buttons: list[list[tuple[str, str]]] = []
        for line in lines:
            buttons.append([
                (f"➖ {line.product.name[:18]}", f"cart:-:{line.product.id}"),
                ("➕", f"cart:+:{line.product.id}"),
            ])
        if lines:
            buttons.append([("✅ Оформить заказ", "checkout")])
        buttons.append([("🏠 Меню", "menu")])
        await self.api.send_message(user_id, cart_text(lines, self.settings.currency), buttons)

    async def _callback(self, user_id: int, callback: dict[str, Any]) -> None:
        payload = callback.get("payload", "")
        callback_id = callback.get("callback_id")
        if callback_id:
            await self.api.answer_callback(str(callback_id))
        if payload == "menu":
            await self._message(user_id, "/menu")
        elif payload == "catalog":
            await self.show_catalog(user_id)
        elif payload == "cart":
            await self.show_cart(user_id)
        elif payload == "about":
            contact = f"\nСвязь с мастером: {html.escape(self.settings.shop_contact)}" if self.settings.shop_contact else ""
            await self.api.send_message(user_id, f"Все товары изготовлены вручную. После заказа мастер свяжется с вами для подтверждения оплаты и доставки.{contact}", self._menu_buttons())
        elif payload.startswith("add:"):
            ok, message = self.db.add_to_cart(self.PLATFORM, str(user_id), int(payload.split(":")[1]))
            await self.api.send_message(user_id, ("✅ " if ok else "⚠️ ") + message, [[("🧺 Корзина", "cart"), ("🛍 Каталог", "catalog")]])
        elif payload.startswith("cart:"):
            _, sign, raw_id = payload.split(":")
            self.db.change_cart(self.PLATFORM, str(user_id), int(raw_id), 1 if sign == "+" else -1)
            await self.show_cart(user_id)
        elif payload == "checkout":
            if not self.db.get_cart(self.PLATFORM, str(user_id)):
                await self.api.send_message(user_id, "Корзина пуста.", self._menu_buttons())
                return
            self.checkouts[user_id] = CheckoutState()
            await self.api.send_message(user_id, "Как вас зовут? Для отмены отправьте /cancel")
        elif payload == "order:confirm":
            await self._complete_order(user_id)
        elif payload == "order:cancel":
            self.checkouts.pop(user_id, None)
            await self.api.send_message(user_id, "Оформление отменено. Корзина сохранена.", self._menu_buttons())

    async def _checkout_input(self, user_id: int, text: str) -> None:
        state = self.checkouts[user_id]
        if not text:
            await self.api.send_message(user_id, "Пожалуйста, отправьте ответ текстом.")
            return
        if state.step == "name":
            state.values["name"] = text[:100]
            state.step = "phone"
            await self.api.send_message(user_id, "Укажите номер телефона:")
        elif state.step == "phone":
            try:
                state.values["phone"] = validate_phone(text)
            except ValueError as exc:
                await self.api.send_message(user_id, str(exc))
                return
            state.step = "address"
            await self.api.send_message(user_id, "Куда доставить заказ? Укажите город, адрес или удобный пункт выдачи:")
        elif state.step == "address":
            state.values["address"] = text[:500]
            state.step = "comment"
            await self.api.send_message(user_id, "Комментарий к заказу? Если его нет, отправьте —")
        elif state.step == "comment":
            state.values["comment"] = "" if text in {"-", "—"} else text[:500]
            state.step = "confirm"
            await self.api.send_message(
                user_id,
                checkout_summary(self.db, self.PLATFORM, str(user_id), state.values, self.settings.currency),
                [[("✅ Подтвердить", "order:confirm"), ("❌ Отмена", "order:cancel")]],
            )

    async def _complete_order(self, user_id: int) -> None:
        state = self.checkouts.get(user_id)
        if state is None or state.step != "confirm":
            await self.api.send_message(user_id, "Сессия оформления устарела. Откройте корзину ещё раз.")
            return
        try:
            order, lines = self.db.create_order(
                self.PLATFORM, str(user_id), state.values["name"], state.values["phone"],
                state.values["address"], state.values.get("comment", ""),
            )
        except ValueError as exc:
            await self.api.send_message(user_id, f"Не удалось оформить заказ: {exc}")
            return
        self.checkouts.pop(user_id, None)
        await self.api.send_message(user_id, f"Спасибо! Заказ №{order.id} принят 🎉\nМастер свяжется с вами для подтверждения оплаты и доставки.", self._menu_buttons())
        notification = order_notification(order.id, self.PLATFORM, str(user_id), state.values["name"], state.values["phone"], state.values["address"], state.values.get("comment", ""), lines, self.settings.currency)
        await self.notify_admins(notification)
