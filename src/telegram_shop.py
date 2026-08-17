from __future__ import annotations

import html
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from .config import Settings
from .db import ShopDatabase
from .shop_common import (
    CheckoutState,
    cart_text,
    checkout_summary,
    existing_image,
    order_notification,
    product_text,
    validate_phone,
)
from .telegram_api import TelegramAPI

logger = logging.getLogger(__name__)
Notifier = Callable[[str], Awaitable[None]]


class TelegramShopBot:
    PLATFORM = "telegram"
    MENU: ClassVar[list[list[str]]] = [["🛍 Каталог", "🧺 Корзина"], ["ℹ️ О магазине"]]

    def __init__(self, api: TelegramAPI, db: ShopDatabase, settings: Settings, notify_admins: Notifier) -> None:
        self.api = api
        self.db = db
        self.settings = settings
        self.notify_admins = notify_admins
        self.checkouts: dict[int, CheckoutState] = {}

    async def handle(self, update: dict[str, Any]) -> None:
        if callback := update.get("callback_query"):
            await self._callback(callback)
        elif message := update.get("message"):
            await self._message(message)

    async def _message(self, message: dict[str, Any]) -> None:
        chat_id = int(message["chat"]["id"])
        text = (message.get("text") or "").strip()
        if text in {"/start", "/menu", "🏠 Меню"}:
            self.checkouts.pop(chat_id, None)
            await self.api.send_message(
                chat_id,
                f"Добро пожаловать в <b>{html.escape(self.settings.shop_name)}</b>! Здесь можно выбрать изделие и оформить заказ.",
                reply_keyboard=self.MENU,
            )
        elif text in {"/catalog", "🛍 Каталог"}:
            await self.show_catalog(chat_id)
        elif text in {"/cart", "🧺 Корзина"}:
            await self.show_cart(chat_id)
        elif text == "ℹ️ О магазине":
            contact = f"\nСвязь с мастером: {html.escape(self.settings.shop_contact)}" if self.settings.shop_contact else ""
            await self.api.send_message(chat_id, f"Все товары изготовлены вручную. После заказа мастер свяжется с вами для подтверждения оплаты и доставки.{contact}")
        elif text == "/cancel":
            self.checkouts.pop(chat_id, None)
            await self.api.send_message(chat_id, "Оформление отменено. Корзина сохранена.", reply_keyboard=self.MENU)
        elif chat_id in self.checkouts:
            await self._checkout_input(chat_id, text)
        else:
            await self.api.send_message(chat_id, "Выберите действие в меню 👇", reply_keyboard=self.MENU)

    async def show_catalog(self, chat_id: int) -> None:
        products = self.db.list_products()
        if not products:
            await self.api.send_message(chat_id, "Сейчас все изделия распроданы. Загляните немного позже 🌿")
            return
        await self.api.send_message(chat_id, f"<b>Каталог — {html.escape(self.settings.shop_name)}</b>")
        for product in products:
            keyboard = [[{"text": "🧺 Добавить в корзину", "callback_data": f"add:{product.id}"}]]
            image = existing_image(product)
            caption = product_text(product, self.settings.currency)
            if image and len(caption) <= 1000:
                await self.api.send_photo(chat_id, image, caption, keyboard)
            elif image:
                await self.api.send_photo(chat_id, image, f"<b>{html.escape(product.name)}</b>")
                await self.api.send_message(chat_id, caption, keyboard)
            else:
                await self.api.send_message(chat_id, caption, keyboard)

    async def show_cart(self, chat_id: int) -> None:
        customer_id = str(chat_id)
        lines = self.db.get_cart(self.PLATFORM, customer_id)
        keyboard: list[list[dict[str, str]]] = []
        for line in lines:
            keyboard.append([
                {"text": f"➖ {line.product.name[:18]}", "callback_data": f"cart:-:{line.product.id}"},
                {"text": "➕", "callback_data": f"cart:+:{line.product.id}"},
            ])
        if lines:
            keyboard.append([{"text": "✅ Оформить заказ", "callback_data": "checkout"}])
        await self.api.send_message(chat_id, cart_text(lines, self.settings.currency), keyboard or None)

    async def _callback(self, callback: dict[str, Any]) -> None:
        chat_id = int(callback["message"]["chat"]["id"])
        data = callback.get("data", "")
        if data.startswith("add:"):
            ok, message = self.db.add_to_cart(self.PLATFORM, str(chat_id), int(data.split(":")[1]))
            await self.api.answer_callback(callback["id"], message)
            if ok:
                logger.info("Товар добавлен в Telegram-корзину пользователя %s", chat_id)
        elif data.startswith("cart:"):
            _, sign, raw_id = data.split(":")
            self.db.change_cart(self.PLATFORM, str(chat_id), int(raw_id), 1 if sign == "+" else -1)
            await self.api.answer_callback(callback["id"], "Корзина обновлена")
            await self.show_cart(chat_id)
        elif data == "checkout":
            if not self.db.get_cart(self.PLATFORM, str(chat_id)):
                await self.api.answer_callback(callback["id"], "Корзина пуста")
                return
            self.checkouts[chat_id] = CheckoutState()
            await self.api.answer_callback(callback["id"])
            await self.api.send_message(chat_id, "Как вас зовут? Для отмены отправьте /cancel")
        elif data == "order:confirm":
            await self.api.answer_callback(callback["id"])
            await self._complete_order(chat_id)
        elif data == "order:cancel":
            self.checkouts.pop(chat_id, None)
            await self.api.answer_callback(callback["id"], "Отменено")
            await self.api.send_message(chat_id, "Оформление отменено. Корзина сохранена.")

    async def _checkout_input(self, chat_id: int, text: str) -> None:
        state = self.checkouts[chat_id]
        if not text:
            await self.api.send_message(chat_id, "Пожалуйста, отправьте ответ текстом.")
            return
        if state.step == "name":
            state.values["name"] = text[:100]
            state.step = "phone"
            await self.api.send_message(chat_id, "Укажите номер телефона:")
        elif state.step == "phone":
            try:
                state.values["phone"] = validate_phone(text)
            except ValueError as exc:
                await self.api.send_message(chat_id, str(exc))
                return
            state.step = "address"
            await self.api.send_message(chat_id, "Куда доставить заказ? Укажите город, адрес или удобный пункт выдачи:")
        elif state.step == "address":
            state.values["address"] = text[:500]
            state.step = "comment"
            await self.api.send_message(chat_id, "Комментарий к заказу? Если его нет, отправьте —")
        elif state.step == "comment":
            state.values["comment"] = "" if text in {"-", "—"} else text[:500]
            state.step = "confirm"
            keyboard = [[
                {"text": "✅ Подтвердить", "callback_data": "order:confirm"},
                {"text": "❌ Отмена", "callback_data": "order:cancel"},
            ]]
            await self.api.send_message(
                chat_id,
                checkout_summary(self.db, self.PLATFORM, str(chat_id), state.values, self.settings.currency),
                keyboard,
            )

    async def _complete_order(self, chat_id: int) -> None:
        state = self.checkouts.get(chat_id)
        if state is None or state.step != "confirm":
            await self.api.send_message(chat_id, "Сессия оформления устарела. Откройте корзину ещё раз.")
            return
        try:
            order, lines = self.db.create_order(
                self.PLATFORM, str(chat_id), state.values["name"], state.values["phone"],
                state.values["address"], state.values.get("comment", ""),
            )
        except ValueError as exc:
            await self.api.send_message(chat_id, f"Не удалось оформить заказ: {exc}")
            return
        self.checkouts.pop(chat_id, None)
        await self.api.send_message(
            chat_id,
            f"Спасибо! Заказ №{order.id} принят 🎉\nМастер свяжется с вами для подтверждения оплаты и доставки.",
            reply_keyboard=self.MENU,
        )
        notification = order_notification(order.id, self.PLATFORM, str(chat_id), state.values["name"], state.values["phone"], state.values["address"], state.values.get("comment", ""), lines, self.settings.currency)
        await self.notify_admins(notification)
