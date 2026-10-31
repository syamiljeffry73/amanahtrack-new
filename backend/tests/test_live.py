"""Live telemetry: door, vibration, heartbeat, WebSocket push, offline sweep."""

import base64
import hashlib
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core import auth
from app.core.fiqh import FundCategory
from app.main import app, get_db
from app.models import (
    AccountNature,
    Alert,
    Base,
    Device,
    DeviceEvent,
    Fund,
    Institution,
    Role,
    User,
    _now,
)
from app.services import alerts as alert_service
from app.services import telemetry

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
SECRET = "live-secret"


@pytest.fixture()
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/live.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    # the websocket route opens its own session, so point it at the test db
    import app.main as main

    original = main.SessionLocal
    main.SessionLocal = Session

    db = Session()
    insts = {}
    for slug in ("masjid-a", "masjid-b"):
        inst = Institution(name=slug, slug=slug)
        db.add(inst)
        db.flush()
        insts[slug] = inst.id
        db.add(
            Fund(
                institution_id=inst.id,
                code="cash",
                category=FundCategory.SADAQAH,
                label_ms="Tunai",
                label_en="Cash",
                nature=AccountNature.ASSET,
            )
        )
    db.add(Device(institution_id=insts["masjid-a"], serial="BOX-L", label="Live box", shared_secret=SECRET))
    for inst_slug, email, role in (
        ("masjid-a", "c@a.my", Role.COMMITTEE),
        ("masjid-b", "o@b.my", Role.COMMITTEE),
    ):
        db.add(
            User(
                institution_id=insts[inst_slug],
                name=email,
                email=email,
                password_hash=auth.hash_password("pw"),
                role=role,
            )
        )
    db.commit()
    db.close()
    yield TestClient(app), Session
    app.dependency_overrides.clear()
    main.SessionLocal = original


def login(c, email):
    r = c.post("/auth/login", data={"username": email, "password": "pw"})
    return r.json()["access_token"]


def send(c, kind, photo=None, pulses=None, bad_sig=False):
    sha = hashlib.sha256(photo).hexdigest() if photo else None
    sig = alert_service.device_signature("BOX-L", kind, sha, SECRET)
    if bad_sig:
        sig = "0" * 64
    body = {"serial": "BOX-L", "kind": kind, "pulses": pulses}
    if photo:
        body["photo_b64"] = base64.b64encode(photo).decode()
        body["photo_sha256"] = sha
    return c.post("/devices/telemetry", json=body, headers={"X-Device-Signature": sig})


def hdr(c, email="c@a.my"):
    return {"Authorization": f"Bearer {login(c, email)}"}


def test_heartbeat_marks_the_box_online_without_logging_an_event(env):
    c, Session = env
    assert send(c, "heartbeat").status_code == 202
    devices = c.get("/i/masjid-a/devices", headers=hdr(c)).json()
    assert devices[0]["online"] is True and devices[0]["last_seen_at"]
    assert c.get("/i/masjid-a/live/events", headers=hdr(c)).json() == []


def test_bad_signature_and_unknown_kind_are_rejected(env):
    c, _ = env
    assert send(c, "door_opened", bad_sig=True).status_code == 401
    sig = alert_service.device_signature("BOX-L", "explode", None, SECRET)
    r = c.post(
        "/devices/telemetry",
        json={"serial": "BOX-L", "kind": "explode"},
        headers={"X-Device-Signature": sig},
    )
    assert r.status_code == 422


def test_door_opened_raises_an_alert_with_a_verifiable_photo(env):
    c, _ = env
    assert send(c, "door_opened", photo=PNG).status_code == 202
    h = hdr(c)
    alert = c.get("/i/masjid-a/alerts", headers=h).json()[0]
    assert alert["kind"] == "door_opened" and alert["has_photo"]
    v = c.post(f"/i/masjid-a/alerts/{alert['id']}/verify", headers=h).json()
    assert v["intact"] is True
    assert c.get("/i/masjid-a/devices", headers=h).json()[0]["door_state"] == "open"
    feed = c.get("/i/masjid-a/live/events", headers=h).json()
    assert feed[0]["kind"] == "door_opened" and feed[0]["alert_id"] == alert["id"]


def test_vibration_with_door_closed_is_critical_and_repeats_are_grouped(env):
    c, _ = env
    send(c, "door_closed")
    send(c, "vibration", pulses=6)
    send(c, "vibration", pulses=4)
    h = hdr(c)
    alerts = c.get("/i/masjid-a/alerts", headers=h).json()
    assert len(alerts) == 1 and alerts[0]["severity"] == "critical"
    feed = c.get("/i/masjid-a/live/events", headers=h).json()
    assert sum(1 for e in feed if e["kind"] == "vibration") == 2
    assert any("grouped" in e["detail"] for e in feed)


def test_vibration_while_door_is_open_is_not_an_alert(env):
    c, _ = env
    send(c, "door_opened")
    send(c, "vibration", pulses=5)
    alerts = c.get("/i/masjid-a/alerts", headers=hdr(c)).json()
    assert [a["kind"] for a in alerts] == ["door_opened"]


def test_websocket_pushes_events_to_the_right_institution_only(env):
    c, _ = env
    ta, tb = login(c, "c@a.my"), login(c, "o@b.my")
    with c.websocket_connect(f"/i/masjid-a/live?token={ta}") as wa, c.websocket_connect(
        f"/i/masjid-b/live?token={tb}"
    ) as wb:
        assert wa.receive_json()["type"] == "hello"
        assert wb.receive_json()["type"] == "hello"
        send(c, "door_opened", photo=PNG)
        types = [wa.receive_json() for _ in range(3)]
        assert [m["type"] for m in types] == ["event", "alert_count", "status"]
        assert types[0]["event"]["kind"] == "door_opened"
        assert types[0]["event"]["has_photo"] is True
        assert types[1]["open"] == 1
        # masjid-b hears nothing: its socket stays quiet. A ping proves it is
        # still open without blocking on a message that must not come.
        wb.send_text("ping")


def test_websocket_refuses_a_bad_token_and_another_institutions_feed(env):
    c, _ = env
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/i/masjid-a/live?token=nonsense"):
            pass
    tb = login(c, "o@b.my")
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect(f"/i/masjid-a/live?token={tb}"):
            pass


def test_sweep_raises_offline_alert_once_and_clears_when_the_box_returns(env):
    c, Session = env
    send(c, "heartbeat")
    db = Session()
    later = _now() + timedelta(seconds=200)
    first = telemetry.sweep(db, later)
    db.commit()
    assert first and first[0][1][0]["event"]["kind"] == "device_offline"
    assert telemetry.sweep(db, later + timedelta(seconds=30)) == []  # not twice
    db.commit()
    db.close()
    assert c.get("/i/masjid-a/alerts", headers=hdr(c)).json()[0]["kind"] == "device_offline"
    send(c, "heartbeat")
    db = Session()
    assert db.query(Device).one().offline_alerted is False
    db.close()


def test_sweep_flags_a_door_left_open_once(env):
    c, Session = env
    send(c, "door_opened")
    db = Session()
    later = _now() + timedelta(seconds=150)
    out = telemetry.sweep(db, later)
    db.commit()
    kinds = [m["event"]["kind"] for _, ms in out for m in ms if m["type"] == "event"]
    assert "door_left_open" in kinds
    assert telemetry.sweep(db, later + timedelta(seconds=30)) == []
    db.close()


def test_box_alert_endpoint_also_lands_in_the_live_feed(env):
    c, _ = env
    sha = hashlib.sha256(PNG).hexdigest()
    sig = alert_service.device_signature("BOX-L", "unexplained_weight", sha, SECRET)
    r = c.post(
        "/devices/alerts",
        json={
            "serial": "BOX-L",
            "kind": "unexplained_weight",
            "photo_b64": base64.b64encode(PNG).decode(),
            "photo_sha256": sha,
        },
        headers={"X-Device-Signature": sig},
    )
    assert r.status_code == 201
    assert c.get("/i/masjid-a/live/events", headers=hdr(c)).json()[0]["kind"] == "unexplained_weight"
