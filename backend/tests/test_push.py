"""Push notifications: token registration and the listener that sends them."""

import asyncio

from sqlalchemy import select

from app.models import PushToken, User
from app.services import push
from test_live import env, hdr  # noqa: F401  (fixtures and helpers)

TOKEN_A = "ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]"
TOKEN_B = "ExponentPushToken[bbbbbbbbbbbbbbbbbbbbbb]"


def reg(c, token, email="c@a.my", slug="masjid-a"):
    return c.post(f"/i/{slug}/push-token", json={"token": token}, headers=hdr(c, email))


def test_register_is_idempotent_and_needs_login(env):
    c, Session = env
    assert c.post("/i/masjid-a/push-token", json={"token": TOKEN_A}).status_code == 401
    assert reg(c, TOKEN_A).status_code == 204
    assert reg(c, TOKEN_A).status_code == 204
    with Session() as db:
        assert len(db.scalars(select(PushToken)).all()) == 1


def test_cannot_register_for_another_institution(env):
    c, _ = env
    assert reg(c, TOKEN_A, email="o@b.my", slug="masjid-a").status_code == 404


def test_sign_out_removes_only_my_token(env):
    c, Session = env
    reg(c, TOKEN_A)
    reg(c, TOKEN_B, email="o@b.my", slug="masjid-b")
    r = c.delete("/i/masjid-a/push-token", params={"token": TOKEN_A}, headers=hdr(c))
    assert r.status_code == 204
    with Session() as db:
        left = [t.token for t in db.scalars(select(PushToken))]
    assert left == [TOKEN_B]


class FakeClient:
    sent: list = []
    reply = None

    def __init__(self, **_):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, url, json, headers):
        FakeClient.sent.append(json)

        class R:
            status_code = 200
            text = ""

            def json(_self):
                return FakeClient.reply or {"data": [{"status": "ok"} for _ in json]}

        return R()


def message(kind="door_opened", alert_id="a1", severity="warning"):
    return {
        "type": "event",
        "event": {
            "id": "e1",
            "kind": kind,
            "detail": "The door switch reported the door open.",
            "device_label": "Tabung pintu utama",
            "alert_id": alert_id,
            "severity": severity,
        },
    }


def run_listener(Session, inst_id, msg):
    listener = push.make_listener(Session, client_factory=FakeClient)
    asyncio.run(listener(inst_id, msg))


def setup_function():
    FakeClient.sent = []
    FakeClient.reply = None
    push._last_sent.clear()


def institution_id(Session):
    with Session() as db:
        return db.scalars(select(User).where(User.email == "c@a.my")).first().institution_id


def test_alert_event_reaches_only_its_own_institution(env):
    c, Session = env
    reg(c, TOKEN_A)
    reg(c, TOKEN_B, email="o@b.my", slug="masjid-b")
    run_listener(Session, institution_id(Session), message())
    assert len(FakeClient.sent) == 1
    batch = FakeClient.sent[0]
    assert [m["to"] for m in batch] == [TOKEN_A]
    assert batch[0]["data"] == {"alert_id": "a1"}
    assert "photo" not in str(batch[0]).lower()


def test_heartbeats_and_plain_events_do_not_push(env):
    c, Session = env
    reg(c, TOKEN_A)
    inst = institution_id(Session)
    run_listener(Session, inst, {"type": "status", "device": {}})
    run_listener(Session, inst, message(alert_id=None))
    assert FakeClient.sent == []


def test_repeats_are_throttled(env):
    c, Session = env
    reg(c, TOKEN_A)
    inst = institution_id(Session)
    run_listener(Session, inst, message())
    run_listener(Session, inst, message())
    assert len(FakeClient.sent) == 1
    run_listener(Session, inst, message(kind="vibration"))
    assert len(FakeClient.sent) == 2


def test_dead_tokens_are_removed(env):
    c, Session = env
    reg(c, TOKEN_A)
    FakeClient.reply = {
        "data": [{"status": "error", "message": "gone", "details": {"error": "DeviceNotRegistered"}}]
    }
    run_listener(Session, institution_id(Session), message())
    with Session() as db:
        assert db.scalars(select(PushToken)).all() == []
