from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)
UpdateHandler = Callable[[dict[str, Any]], Awaitable[None]]


class TelegramAPI:
    def __init__(self, token: str, session: aiohttp.ClientSession) -> None:
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._file_url = f"https://api.telegram.org/file/bot{token}"
        self._session = session
        self._offset: int | None = None

    async def call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        async with self._session.post(f"{self._base_url}/{method}", json=payload or {}) as response:
            data = await response.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram {method}: {data.get('description', data)}")
            return data.get("result")

    async def send_message(
        self,
        chat_id: int,
        text: str,
        keyboard: list[list[dict[str, str]]] | None = None,
        *,
        reply_keyboard: list[list[str]] | None = None,
    ) -> Any:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        elif reply_keyboard:
            payload["reply_markup"] = {
                "keyboard": [[{"text": text} for text in row] for row in reply_keyboard],
                "resize_keyboard": True,
            }
        return await self.call("sendMessage", payload)

    async def send_photo(
        self, chat_id: int, image_path: Path, caption: str, keyboard: list[list[dict[str, str]]] | None = None
    ) -> Any:
        form = aiohttp.FormData()
        form.add_field("chat_id", str(chat_id))
        form.add_field("caption", caption)
        form.add_field("parse_mode", "HTML")
        if keyboard:
            import json

            form.add_field("reply_markup", json.dumps({"inline_keyboard": keyboard}, ensure_ascii=False))
        with image_path.open("rb") as image:
            form.add_field("photo", image, filename=image_path.name, content_type="application/octet-stream")
            async with self._session.post(f"{self._base_url}/sendPhoto", data=form) as response:
                data = await response.json()
                if not data.get("ok"):
                    raise RuntimeError(f"Telegram sendPhoto: {data.get('description', data)}")
                return data.get("result")

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        await self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})

    async def download_file(self, file_id: str, destination: Path) -> None:
        file_info = await self.call("getFile", {"file_id": file_id})
        async with self._session.get(f"{self._file_url}/{file_info['file_path']}") as response:
            response.raise_for_status()
            destination.write_bytes(await response.read())

    async def delete_webhook(self) -> None:
        await self.call("deleteWebhook", {"drop_pending_updates": False})

    async def poll(self, handler: UpdateHandler) -> None:
        await self.delete_webhook()
        while True:
            try:
                payload: dict[str, Any] = {"timeout": 30, "allowed_updates": ["message", "callback_query"]}
                if self._offset is not None:
                    payload["offset"] = self._offset
                updates = await self.call("getUpdates", payload)
                for update in updates:
                    self._offset = int(update["update_id"]) + 1
                    try:
                        await handler(update)
                    except Exception:
                        logger.exception("Ошибка обработки Telegram update %s", update.get("update_id"))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Ошибка Telegram polling; повтор через 3 секунды")
                await asyncio.sleep(3)
