"""
Real-time fan-out to the app.

One process, in memory: a dict of institution id to the WebSockets connected
for it. That is enough for a single server worker, which is what this project
runs. With several workers each would only know its own sockets, and the
broadcast would need a shared channel such as Redis pub/sub in the middle.

Messages are small JSON objects with a "type": "event" (something happened),
"status" (a box heartbeat or online change) or "alert_count" (badge refresh).
Photos are never pushed over the socket. The message says an alert has a photo
and the app fetches it with its normal authenticated request.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from fastapi import WebSocket

log = logging.getLogger("amanahtrack.live")


class Hub:
    def __init__(self) -> None:
        self._sockets: dict[str, set[WebSocket]] = defaultdict(set)
        # Called (in the background) after every broadcast. Push notifications
        # hook in here so every place that raises an alert is covered.
        self.listeners: list = []

    def add(self, institution_id: str, ws: WebSocket) -> None:
        self._sockets[institution_id].add(ws)

    def remove(self, institution_id: str, ws: WebSocket) -> None:
        self._sockets[institution_id].discard(ws)
        if not self._sockets[institution_id]:
            self._sockets.pop(institution_id, None)

    def count(self, institution_id: str) -> int:
        return len(self._sockets.get(institution_id, ()))

    async def broadcast(self, institution_id: str, message: dict) -> None:
        for listener in self.listeners:
            asyncio.create_task(self._run_listener(listener, institution_id, message))
        dead = []
        for ws in list(self._sockets.get(institution_id, ())):
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=3)
            except Exception:  # a slow or closed phone must not block the rest
                dead.append(ws)
        for ws in dead:
            self.remove(institution_id, ws)
            log.info("dropped a dead live socket")


    @staticmethod
    async def _run_listener(listener, institution_id: str, message: dict) -> None:
        try:
            await listener(institution_id, message)
        except Exception:  # a failing side effect must never break the live feed
            log.exception("live listener failed")


hub = Hub()
