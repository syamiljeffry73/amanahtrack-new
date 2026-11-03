"""
Push notifications through Expo's push service.

When the box raises an alert, every phone registered for that institution gets
a notification, even if the app is closed. The message carries only the title,
a short line and the alert id. The photo and the evidence stay behind the
login: tapping the notification opens the alert screen, which asks for them
with the normal authenticated request.

Door opened and tamper alerts are urgent. Repeats of the same kind within a
short window are skipped so a shaken box does not buzz the phone every second.
"""

from __future__ import annotations

import logging
import os
import time

import httpx
from sqlalchemy import delete, select

from app.models import PushToken, User

log = logging.getLogger("amanahtrack.push")

EXPO_PUSH_URL = os.getenv("AMANAH_EXPO_PUSH_URL", "https://exp.host/--/api/v2/push/send")
ENABLED = os.getenv("AMANAH_PUSH", "on").lower() != "off"
THROTTLE_S = float(os.getenv("AMANAH_PUSH_THROTTLE_S", "20"))

_last_sent: dict[tuple[str, str], float] = {}

_TITLES = {
    "critical": "Critical alert",
    "warning": "Alert from the box",
    "info": "Box update",
}


def _should_send(institution_id: str, message: dict) -> bool:
    if not ENABLED or message.get("type") != "event":
        return False
    event = message.get("event") or {}
    if not event.get("alert_id"):  # plain telemetry such as heartbeats
        return False
    key = (institution_id, str(event.get("kind")))
    now = time.monotonic()
    if now - _last_sent.get(key, 0.0) < THROTTLE_S:
        return False
    _last_sent[key] = now
    return True


def build_messages(tokens: list[str], message: dict) -> list[dict]:
    event = message["event"]
    severity = event.get("severity") or "info"
    body = event.get("detail") or event.get("kind", "").replace("_", " ")
    device = event.get("device_label")
    if device:
        body = f"{device}: {body}"
    return [
        {
            "to": token,
            "title": _TITLES.get(severity, "Box update"),
            "body": body[:140],
            "sound": "default",
            "priority": "high",
            "channelId": "alerts",
            "data": {"alert_id": event["alert_id"]},
        }
        for token in tokens
    ]


def make_listener(session_factory, client_factory=httpx.AsyncClient):
    async def listener(institution_id: str, message: dict) -> None:
        if not _should_send(institution_id, message):
            return
        with session_factory() as db:
            tokens = list(
                db.scalars(
                    select(PushToken.token)
                    .join(User, User.id == PushToken.user_id)
                    .where(PushToken.institution_id == institution_id, User.active.is_(True))
                )
            )
        if not tokens:
            return
        payload = build_messages(tokens, message)
        async with client_factory(timeout=10) as client:
            resp = await client.post(
                EXPO_PUSH_URL,
                json=payload,
                headers={"Accept": "application/json"},
            )
        if resp.status_code != 200:
            log.warning("expo push returned %s: %s", resp.status_code, resp.text[:200])
            return
        # Drop tokens Expo says no longer exist (app uninstalled).
        dead = []
        for token, ticket in zip(tokens, resp.json().get("data", [])):
            if ticket.get("status") == "error":
                log.warning("push to a phone failed: %s", ticket.get("message"))
                if (ticket.get("details") or {}).get("error") == "DeviceNotRegistered":
                    dead.append(token)
        if dead:
            with session_factory() as db:
                db.execute(delete(PushToken).where(PushToken.token.in_(dead)))
                db.commit()

    return listener
