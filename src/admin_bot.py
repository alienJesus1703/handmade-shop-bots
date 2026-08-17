from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

from .config import Settings
from .db import ShopDatabase
from .models import format_money, parse_price
from .shop_common import existing_image, product_text
from .telegram_api import TelegramAPI

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProductDraft:
    step: str = "name"
    values: dict[str, Any] = field(default_factory=dict)


class TelegramAdminBot:
    MENU: ClassVar[list[list[str]]] = [["➕ Добавить товар", "📦 Товары"], ["🧾 Заказы"]]

    def __init__(self, api: TelegramAPI, db: ShopDatabase, settings: Settings) -> None:
        self.api = api
        self.db = db
        self.settings = settings
        self.drafts: dict[int, ProductDraft] = {}

    def _authorized(self, user_id: int) -> bool:
        return user_id in self.settings.admin_telegram_ids

    async def notify_admins(self, text: str) -> None:
        for admin_id in self.settings.admin_telegram_ids:
            try:
                await self.api.send_message(admin_id, text)
            except Exception:
                logger.exception("Не удалось уведомить администратора %s", admin_id)

    async def handle(self, update: dict[str, Any]) -> None:
        if callback := update.get("callback_query"):
            user_id = int(callback["from"]["id"])
            if not self._authorized(user_id):
                await self.api.answer_callback(callback["id"], "Нет доступа")
                return
            await self._callback(callback)
            return
        message = update.get("message")
        if not message:
            return
        user_id = int(message["from"]["id"])
        chat_id = int(message["chat"]["id"])
        if not self._authorized(user_id):
            await self.api.send_message(chat_id, "Доступ запрещён.")
            return
        await self._message(chat_id, message)

    async def _message(self, chat_id: int, message: dict[str, Any]) -> None:
        text = (message.get("text") or "").strip()
        if text in {"/start", "/menu"}:
            self.drafts.pop(chat_id, None)
            await self.api.send_message(chat_id, "Панель управления магазином.", reply_keyboard=self.MENU)
        elif text in {"/add", "➕ Добавить товар"}:
            self.drafts[chat_id] = ProductDraft()
            await self.api.send_message(chat_id, "Введите название товара (до 100 символов). Для отмены: /cancel")
        elif text in {"/products", "📦 Товары"}:
            await self.show_products(chat_id)
        elif text in {"/orders", "🧾 Заказы"}:
            await self.show_orders(chat_id)
        elif text == "/cancel":
            self.drafts.pop(chat_id, None)
            await self.api.send_message(chat_id, "Действие отменено.", reply_keyboard=self.MENU)
        elif chat_id in self.drafts:
            await self._draft_input(chat_id, message)
        else:
            await self.api.send_message(chat_id, "Выберите действие 👇", reply_keyboard=self.MENU)

    async def _draft_input(self, chat_id: int, message: dict[str, Any]) -> None:
        draft = self.drafts[chat_id]
        text = (message.get("text") or "").strip()
        if draft.step == "name":
            if not text or len(text) > 100:
                await self.api.send_message(chat_id, "Название должно содержать от 1 до 100 символов.")
                return
            draft.values["name"] = text
            draft.step = "description"
            await self.api.send_message(chat_id, "Введите описание товара:")
        elif draft.step == "description":
            if not text or len(text) > 3000:
                await self.api.send_message(chat_id, "Описание должно содержать от 1 до 3000 символов.")
                return
            draft.values["description"] = text
            draft.step = "price"
            await self.api.send_message(chat_id, "Введите цену, например 1500 или 1500,50:")
        elif draft.step == "price":
            try:
                draft.values["price_kopecks"] = parse_price(text)
            except ValueError as exc:
                await self.api.send_message(chat_id, str(exc))
                return
            draft.step = "stock"
            await self.api.send_message(chat_id, "Сколько штук в наличии?")
        elif draft.step == "stock":
            try:
                stock = int(text)
                if not 0 <= stock <= 1_000_000:
                    raise ValueError
            except ValueError:
                await self.api.send_message(chat_id, "Введите целое число от 0 до 1000000.")
                return
            draft.values["stock"] = stock
            draft.step = "photo"
            await self.api.send_message(chat_id, "Отправьте фотографию товара. Если фото не нужно — /skip")
        elif draft.step == "photo":
            if text == "/skip":
                await self._save_product(chat_id, None)
                return
            photos = message.get("photo") or []
            if not photos:
                await self.api.send_message(chat_id, "Отправьте изображение как фото или используйте /skip.")
                return
            destination = self.settings.images_dir / f"{uuid4().hex}.jpg"
            try:
                await self.api.download_file(photos[-1]["file_id"], destination)
                await self._save_product(chat_id, destination)
            except Exception:
                destination.unlink(missing_ok=True)
                logger.exception("Не удалось сохранить фотографию товара")
                await self.api.send_message(chat_id, "Не удалось загрузить фото. Попробуйте ещё раз или отправьте /skip.")

    async def _save_product(self, chat_id: int, image_path: Path | None) -> None:
        draft = self.drafts[chat_id]
        product_id = self.db.add_product(
            draft.values["name"], draft.values["description"], draft.values["price_kopecks"],
            draft.values["stock"], str(image_path) if image_path else None,
        )
        self.drafts.pop(chat_id, None)
        await self.api.send_message(
            chat_id,
            f"✅ Товар №{product_id} «{html.escape(draft.values['name'])}» опубликован в Telegram и MAX.",
            reply_keyboard=self.MENU,
        )

    async def show_products(self, chat_id: int) -> None:
        products = self.db.list_products(active_only=False)
        if not products:
            await self.api.send_message(chat_id, "Товаров ещё нет. Нажмите «➕ Добавить товар».")
            return
        await self.api.send_message(chat_id, f"Всего товаров: {len(products)}")
        for product in products:
            status = "🟢 опубликован" if product.active else "⚪ скрыт"
            caption = f"{product_text(product, self.settings.currency)}\nСтатус: {status}\nID: <code>{product.id}</code>"
            keyboard = [[
                {"text": "🙈 Скрыть" if product.active else "👁 Опубликовать", "callback_data": f"toggle:{product.id}"},
                {"text": "🗑 Удалить", "callback_data": f"delete_ask:{product.id}"},
            ]]
            image = existing_image(product)
            if image and len(caption) <= 1000:
                await self.api.send_photo(chat_id, image, caption, keyboard)
            elif image:
                await self.api.send_photo(chat_id, image, f"<b>{html.escape(product.name)}</b>")
                await self.api.send_message(chat_id, caption, keyboard)
            else:
                await self.api.send_message(chat_id, caption, keyboard)

    async def show_orders(self, chat_id: int) -> None:
        orders = self.db.recent_orders()
        if not orders:
            await self.api.send_message(chat_id, "Заказов пока нет.")
            return
        rows = ["<b>Последние заказы</b>"]
        for order in orders:
            rows.append(
                f"\n<b>№{order.id}</b> · {html.escape(order.platform)} · {format_money(order.total_kopecks, self.settings.currency)}"
                f"\n{html.escape(order.customer_name)}, {html.escape(order.phone)}"
                f"\n{html.escape(order.address)}"
            )
        text = "\n".join(rows)
        await self.api.send_message(chat_id, text[:4000])

    async def _callback(self, callback: dict[str, Any]) -> None:
        chat_id = int(callback["message"]["chat"]["id"])
        data = callback.get("data", "")
        if data.startswith("toggle:"):
            product_id = int(data.split(":")[1])
            active = self.db.toggle_product(product_id)
            message = "Товар опубликован" if active else "Товар скрыт"
            if active is None:
                message = "Товар не найден"
            await self.api.answer_callback(callback["id"], message)
            await self.show_products(chat_id)
        elif data.startswith("delete_ask:"):
            product_id = int(data.split(":")[1])
            await self.api.answer_callback(callback["id"])
            await self.api.send_message(chat_id, f"Точно удалить товар №{product_id}?", [[
                {"text": "Да, удалить", "callback_data": f"delete_yes:{product_id}"},
                {"text": "Нет", "callback_data": "delete_no"},
            ]])
        elif data.startswith("delete_yes:"):
            product_id = int(data.split(":")[1])
            deleted, image_path = self.db.delete_product(product_id)
            if image_path:
                self._remove_managed_image(Path(image_path))
            await self.api.answer_callback(callback["id"], "Товар удалён" if deleted else "Товар не найден")
            await self.api.send_message(chat_id, f"Товар №{product_id} удалён." if deleted else "Товар уже был удалён.")
        elif data == "delete_no":
            await self.api.answer_callback(callback["id"], "Удаление отменено")

    def _remove_managed_image(self, image_path: Path) -> None:
        try:
            resolved_image = image_path.resolve()
            resolved_root = self.settings.images_dir.resolve()
            if resolved_image.is_relative_to(resolved_root):
                resolved_image.unlink(missing_ok=True)
        except OSError:
            logger.exception("Не удалось удалить изображение %s", image_path)
