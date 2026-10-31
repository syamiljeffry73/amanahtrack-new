"""
Turning raw box telemetry into events and alerts.

The box only reports what its sensors saw: the door switch changed, the
vibration sensor fired, I am still alive. The decisions live here, on the
server, so a stolen or reflashed box cannot decide what counts as suspicious,
and the rules can change without touching the Pi.

Rules:
  door opened                       warning alert, with a photo if there is one
  door closed                       event only
  vibration, door closed            critical alert (nobody should be touching it)
  vibration, door open              event only (someone is counting)
  repeated vibration inside 60 s    event only, so a shaken box is one alert
  no heartbeat for 90 s             device offline alert
  door open for more than 120 s     door left open alert
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Alert,
    AlertKind,
    AlertSeverity,
    Device,
    DeviceEvent,
    Institution,
    _now,
)
from app.services import alerts as alert_service

EVENT_KINDS = {"heartbeat", "door_opened", "door_closed", "vibration"}
OFFLINE_AFTER = timedelta(seconds=90)
DOOR_OPEN_LIMIT = timedelta(seconds=120)
VIBRATION_COOLDOWN = timedelta(seconds=60)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite hands datetimes back naive. They are always UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime | None) -> str | None:
    dt = aware(dt)
    return dt.isoformat() if dt else None


def is_online(device: Device, now: datetime | None = None) -> bool:
    seen = aware(device.last_seen_at)
    if seen is None:
        return False
    return (now or _now()) - seen <= OFFLINE_AFTER


def device_status(device: Device) -> dict:
    return {
        "serial": device.serial,
        "label": device.label,
        "online": is_online(device),
        "last_seen_at": iso(device.last_seen_at),
        "door_state": device.door_state,
        "door_opened_at": iso(device.door_opened_at),
    }


def event_message(db: Session, event: DeviceEvent, alert: Alert | None) -> dict:
    device = db.get(Device, event.device_id) if event.device_id else None
    return {
        "type": "event",
        "event": {
            "id": event.id,
            "kind": event.kind,
            "detail": event.detail,
            "device_label": device.label if device else None,
            "occurred_at": iso(event.occurred_at),
            "alert_id": alert.id if alert else None,
            "severity": alert.severity.value if alert else None,
            "has_photo": bool(alert and alert.photo is not None),
        },
    }


def _log(db: Session, device: Device, kind: str, detail: str, alert, occurred_at) -> DeviceEvent:
    ev = DeviceEvent(
        institution_id=device.institution_id,
        device_id=device.id,
        kind=kind,
        detail=detail,
        alert_id=alert.id if alert else None,
        occurred_at=occurred_at or _now(),
    )
    db.add(ev)
    db.flush()
    return ev


def handle(
    db: Session,
    device: Device,
    *,
    kind: str,
    detail: str = "",
    pulses: int | None = None,
    photo: bytes | None = None,
    photo_sha256: str | None = None,
    signature: str | None = None,
    occurred_at: datetime | None = None,
) -> dict:
    """Apply one telemetry message. Returns what to broadcast."""
    now = _now()
    device.last_seen_at = now
    came_back = device.offline_alerted
    device.offline_alerted = False

    out = {"event": None, "alert": None, "came_back": came_back}

    if kind == "heartbeat":
        return out

    alert = None

    if kind == "door_opened":
        device.door_state = "open"
        device.door_opened_at = now
        device.door_alerted = False
        alert = alert_service.create_alert(
            db,
            institution_id=device.institution_id,
            kind=AlertKind.DOOR_OPENED,
            severity=AlertSeverity.WARNING,
            title="Box door opened",
            detail=detail or "The door switch reported the door open.",
            device=device,
            photo=photo,
            claimed_sha256=photo_sha256,
            signature=signature,
            occurred_at=occurred_at,
        )

    elif kind == "door_closed":
        device.door_state = "closed"
        device.door_alerted = False

    elif kind == "vibration":
        text = detail or (f"{pulses} pulses" if pulses else "Vibration detected")
        if device.door_state == "open":
            detail = f"{text} (door open, counting)"
        else:
            recent = db.scalars(
                select(Alert)
                .where(
                    Alert.device_id == device.id,
                    Alert.kind == AlertKind.VIBRATION,
                )
                .order_by(Alert.seq.desc())
                .limit(1)
            ).first()
            if recent and now - aware(recent.created_at) < VIBRATION_COOLDOWN:
                detail = f"{text} (repeat, grouped with the last alert)"
            else:
                detail = text
                alert = alert_service.create_alert(
                    db,
                    institution_id=device.institution_id,
                    kind=AlertKind.VIBRATION,
                    severity=AlertSeverity.CRITICAL,
                    title="Vibration while the box is closed",
                    detail=text,
                    device=device,
                    photo=photo,
                    claimed_sha256=photo_sha256,
                    signature=signature,
                    occurred_at=occurred_at,
                )

    event = _log(db, device, kind, detail, alert, occurred_at)
    out["event"], out["alert"] = event, alert
    return out


def sweep(db: Session, now: datetime | None = None) -> list[tuple[str, list[dict]]]:
    """Periodic check for things that stop happening: heartbeats, closing a door.

    Returns (institution_id, messages) pairs for the caller to broadcast.
    """
    now = now or _now()
    result: dict[str, list[dict]] = {}
    for device in db.scalars(select(Device).where(Device.active.is_(True))).all():
        msgs = result.setdefault(device.institution_id, [])
        seen = aware(device.last_seen_at)

        if seen and now - seen > OFFLINE_AFTER and not device.offline_alerted:
            device.offline_alerted = True
            alert = alert_service.create_alert(
                db,
                institution_id=device.institution_id,
                kind=AlertKind.DEVICE_OFFLINE,
                severity=AlertSeverity.WARNING,
                title="Box went offline",
                detail=f"No heartbeat since {seen.strftime('%H:%M:%S')} UTC.",
                device=device,
            )
            ev = _log(db, device, "device_offline", alert.detail, alert, now)
            msgs.append(event_message(db, ev, alert))
            msgs.append({"type": "status", "device": device_status(device)})

        opened = aware(device.door_opened_at)
        if (
            device.door_state == "open"
            and opened
            and not device.door_alerted
            and now - opened > DOOR_OPEN_LIMIT
        ):
            device.door_alerted = True
            alert = alert_service.create_alert(
                db,
                institution_id=device.institution_id,
                kind=AlertKind.DOOR_LEFT_OPEN,
                severity=AlertSeverity.WARNING,
                title="Box door left open",
                detail="The door has been open for more than 2 minutes.",
                device=device,
            )
            ev = _log(db, device, "door_left_open", alert.detail, alert, now)
            msgs.append(event_message(db, ev, alert))

    return [(inst, m) for inst, m in result.items() if m]
