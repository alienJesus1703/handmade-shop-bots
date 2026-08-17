from __future__ import annotations

import asyncio
import hmac
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp
from aiohttp import web

logger = logging.getLogger(__name__)
UpdateHandler = Callable[[dict[str, Any]], Awaitable[None]]


class MaxAPI:
    """Small async client for the documented MAX Bot API."""

    def __init__(self, token: str, session: aiohttp.ClientSession) -> None:
        self._base_url = "https://platform-api2.max.ru"
        self._headers = {"Authorization": token}
        self._session = session
        self._marker: int | None = None
        self._image_tokens: dict[str, str] = {}

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        async with self._session.request(
            method, f"{self._base_url}{path}", headers=self._headers, params=params, json=json
        ) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                raise RuntimeError(f"MAX {method} {path}: HTTP {response.status}: {data}")
            return data

    @staticmethod
    def keyboard(buttons: list[list[tuple[str, str]]]) -> dict[str, Any]:
        return {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [
                    [{"type": "callback", "text": text, "payload": payload} for text, payload in row]
                    for row in buttons
                ]
            },
        }

    async def send_message(
        self,
        user_id: int,
        text: str,
        buttons: list[list[tuple[str, str]]] | None = None,
        image_path: Path | None = None,
    ) -> Any:
        attachments: list[dict[str, Any]] = []
        if image_path and image_path.exists():
            token = await self.upload_image(image_path)
            attachments.append({"type": "image", "payload": {"token": token}})
        if buttons:
            attachments.append(self.keyboard(buttons))
        body: dict[str, Any] = {"text": text, "format": "html"}
        if attachments:
            body["attachments"] = attachments
        return await self.request("POST", "/messages", params={"user_id": user_id}, json=body)

    async def answer_callback(self, callback_id: str) -> None:
        await self.request("POST", "/answers", params={"callback_id": callback_id}, json={})

    async def upload_image(self, path: Path) -> str:
        cache_key = f"{path.resolve()}:{path.stat().st_mtime_ns}"
        if token := self._image_tokens.get(cache_key):
            return token
        upload = await self.request("POST", "/uploads", params={"type": "image"})
        form = aiohttp.FormData()
        with path.open("rb") as image:
            form.add_field("data", image, filename=path.name, content_type="application/octet-stream")
            async with self._session.post(upload["url"], data=form) as response:
                result = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(f"MAX upload: HTTP {response.status}: {result}")
        token = result.get("token") or upload.get("token")
        if not token:
            raise RuntimeError(f"MAX не вернул token после загрузки изображения: {result}")
        self._image_tokens[cache_key] = str(token)
        return str(token)

    async def poll(self, handler: UpdateHandler) -> None:
        while True:
            try:
                params: dict[str, Any] = {
                    "timeout": 30,
                    "types": "message_created,message_callback,bot_started",
                }
                if self._marker is not None:
                    params["marker"] = self._marker
                data = await self.request("GET", "/updates", params=params)
                if data.get("marker") is not None:
                    self._marker = int(data["marker"])
                for update in data.get("updates", []):
                    try:
                        await handler(update)
                    except Exception:
                        logger.exception("Ошибка обработки MAX update")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Ошибка MAX polling; повтор через 3 секунды")
                await asyncio.sleep(3)

    async def serve_webhook(self, handler: UpdateHandler, public_url: str, secret: str, port: int) -> None:
        if not public_url.startswith("https://"):
            raise ValueError("MAX_WEBHOOK_URL должен начинаться с https://")
        if not (5 <= len(secret) <= 256) or re.fullmatch(r"[A-Za-z0-9_-]+", secret) is None:
            raise ValueError("MAX_WEBHOOK_SECRET: 5–256 латинских букв, цифр, '_' или '-'")
        route = urlparse(public_url).path or "/max-webhook"
        background_tasks: set[asyncio.Task[None]] = set()

        def task_done(task: asyncio.Task[None]) -> None:
            background_tasks.discard(task)
            if not task.cancelled() and (error := task.exception()):
                logger.error("Ошибка обработки MAX webhook", exc_info=(type(error), error, error.__traceback__))

        async def receive(request: web.Request) -> web.Response:
            supplied = request.headers.get("X-Max-Bot-Api-Secret", "")
            if not hmac.compare_digest(supplied, secret):
                return web.Response(status=401)
            update = await request.json()
            task = asyncio.create_task(handler(update))
            background_tasks.add(task)
            task.add_done_callback(task_done)
            return web.json_response({"ok": True})

        app = web.Application(client_max_size=2 * 1024 * 1024)
        app.router.add_post(route, receive)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        try:
            await self.request(
                "POST",
                "/subscriptions",
                json={
                    "url": public_url,
                    "update_types": ["message_created", "message_callback", "bot_started"],
                    "secret": secret,
                },
            )
            logger.info("MAX webhook запущен: %s -> :%s%s", public_url, port, route)
            await asyncio.Future()
        finally:
            for task in background_tasks:
                task.cancel()
            if background_tasks:
                await asyncio.gather(*background_tasks, return_exceptions=True)
            await runner.cleanup()
