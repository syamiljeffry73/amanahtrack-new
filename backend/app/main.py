"""AmanahTrack API."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core import auth
from app.core.auth import assert_same_institution, require_roles
from app.core.fiqh import FUND_RULES, FundCategory, PurposeCode, rule_for
from app.models import (
    Alert,
    AlertKind,
    AlertSeverity,
    AlertStatus,
    Base,
    Seal,
    Channel,
    Role,
    User,
    CountSession,
    Device,
    DeviceEvent,
    Fund,
    PushToken,
    Institution,
    Transaction,
    TxnStatus,
)
from app.services import alerts as alert_service
from app.services import ledger, sealing, telemetry
from app.services import push as push_service
from app.services.live import hub
from app.services.ledger import FundRuleViolation, LedgerError

# SQLite by default so a fresh clone runs with no setup. Point
# AMANAH_DATABASE_URL at Postgres when you are ready; nothing else changes.
DATABASE_URL = os.getenv("AMANAH_DATABASE_URL", "sqlite:///./amanahtrack.db")
# Hosts such as Neon hand out plain "postgres://" or "postgresql://" links.
# Tell SQLAlchemy to use the psycopg 3 driver for them.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgresql://"):]

_connect_args = (
    {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)
engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
hub.listeners.append(push_service.make_listener(lambda: SessionLocal()))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_user(
    token: str = Depends(auth.oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    return auth.get_current_user(token=token, db=db)


def treasurer_or_admin(user: User = Depends(current_user)) -> User:
    return require_roles(Role.TREASURER, Role.ADMIN)(user)


def committee_reviewer(user: User = Depends(current_user)) -> User:
    """Who may act on an alert.

    The treasurer is left out on purpose. The people who handle the cash should
    not be the ones who clear the alerts about it.
    """
    return require_roles(Role.COMMITTEE, Role.ADMIN, Role.AUDITOR)(user)


def any_member(user: User = Depends(current_user)) -> User:
    return user


async def _sweeper():
    """Every 30 seconds: notice boxes that went quiet and doors left open."""
    while True:
        await asyncio.sleep(30)
        try:
            with SessionLocal() as db:
                batches = telemetry.sweep(db)
                db.commit()
            for inst_id, messages in batches:
                for m in messages:
                    await hub.broadcast(inst_id, m)
        except Exception:  # the sweeper must never die
            import logging

            logging.getLogger("amanahtrack").exception("sweep failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    task = asyncio.create_task(_sweeper())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(
    title="AmanahTrack",
    version="0.1.0",
    description="Shariah-constrained fund ledger for Islamic institutions",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ schemas


class FundOut(BaseModel):
    id: str
    code: str
    category: FundCategory
    label_ms: str
    label_en: str
    balance: Decimal
    provisional: Decimal
    spendable: bool
    constraint_note: str


class DonationIn(BaseModel):
    fund_code: str
    amount: Decimal = Field(gt=0)
    description: str
    channel: Channel = Channel.DASHBOARD


class DisbursementIn(BaseModel):
    fund_code: str
    amount: Decimal = Field(gt=0)
    purpose: PurposeCode
    description: str


class SpendCheckIn(BaseModel):
    fund_code: str
    purpose: PurposeCode


class DeviceEventIn(BaseModel):
    serial: str
    event_type: str = "insertion"
    estimated_amount: Decimal | None = None
    confidence: Decimal | None = None
    occurred_at: datetime | None = None


class DeviceAlertIn(BaseModel):
    serial: str
    kind: AlertKind
    detail: str = ""
    photo_b64: str | None = None
    photo_sha256: str | None = None
    occurred_at: datetime | None = None


class TelemetryIn(BaseModel):
    serial: str
    kind: str
    detail: str = ""
    pulses: int | None = None
    photo_b64: str | None = None
    photo_sha256: str | None = None
    occurred_at: datetime | None = None


class ReviewIn(BaseModel):
    action: str
    note: str | None = Field(default=None, max_length=1000)


class CountIn(BaseModel):
    device_serial: str | None = None
    counted_total: Decimal = Field(ge=0)
    witness_count: int = 1
    note: str | None = None


class TxnOut(BaseModel):
    reference: str
    description: str
    occurred_at: datetime
    channel: Channel
    status: TxnStatus
    amount: Decimal


# ---------------------------------------------------------------- helpers


def get_institution(db: Session, slug: str) -> Institution:
    inst = db.scalars(select(Institution).where(Institution.slug == slug)).first()
    if inst is None:
        raise HTTPException(404, f"No institution with slug '{slug}'")
    return inst


def get_fund(db: Session, inst: Institution, code: str) -> Fund:
    fund = db.scalars(
        select(Fund).where(Fund.institution_id == inst.id, Fund.code == code)
    ).first()
    if fund is None:
        raise HTTPException(404, f"No fund '{code}' at this institution")
    return fund


def cash_fund(db: Session, inst: Institution) -> Fund:
    return get_fund(db, inst, "cash")


def _txn_amount(txn: Transaction) -> Decimal:
    return sum(
        (e.amount for e in txn.entries if e.side.value == "credit"), Decimal("0")
    )


# ------------------------------------------------------------------ routes


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    name: str
    role: Role
    institution_slug: str


@app.post("/auth/login", response_model=LoginOut)
def login(
    form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)
):
    """Sign in. Username field carries the email."""
    user = auth.authenticate(db, form.username, form.password)
    if user is None:
        # Same message for unknown email and wrong password, so the endpoint
        # cannot be used to discover which accounts exist.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    inst = db.get(Institution, user.institution_id)
    return LoginOut(
        access_token=auth.create_token(user),
        name=user.name,
        role=user.role,
        institution_slug=inst.slug,
    )


@app.get("/auth/me")
def whoami(user: User = Depends(current_user), db: Session = Depends(get_db)):
    inst = db.get(Institution, user.institution_id)
    return {
        "name": user.name,
        "role": user.role.value,
        "institution": inst.name,
        "institution_slug": inst.slug,
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/rules")
def list_rules():
    """The fiqh rules as data, so they can be reviewed without reading code."""
    return [
        {
            "category": cat.value,
            "label_ms": rule.label_ms,
            "label_en": rule.label_en,
            "spendable": rule.spendable,
            "basis": rule.basis,
            "notes": rule.notes,
            "allowed_purposes": (
                None
                if rule.allowed_purposes is None
                else sorted(p.value for p in rule.allowed_purposes)
            ),
        }
        for cat, rule in FUND_RULES.items()
    ]


@app.get("/i/{slug}/funds", response_model=list[FundOut])
def list_funds(
    slug: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    inst = get_institution(db, slug)
    assert_same_institution(user, inst)
    funds = db.scalars(
        select(Fund).where(Fund.institution_id == inst.id, Fund.active.is_(True))
    ).all()

    out = []
    for f in funds:
        rule = rule_for(f.category)
        confirmed = ledger.fund_balance(db, f.id)
        with_prov = ledger.fund_balance(db, f.id, include_provisional=True)
        note = rule.notes.split(".")[0] + "."
        if f.bound_purpose:
            note = f"Bound by donor intent to {f.bound_purpose.value.replace('_', ' ')}."
        out.append(
            FundOut(
                id=f.id,
                code=f.code,
                category=f.category,
                label_ms=f.label_ms,
                label_en=f.label_en,
                balance=confirmed,
                provisional=with_prov - confirmed,
                spendable=rule.spendable,
                constraint_note=note,
            )
        )
    return out


@app.post("/i/{slug}/check-spend")
def check_spend(
    slug: str,
    body: SpendCheckIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Preview a rule decision without writing anything.

    The app calls this as the treasurer picks a fund, so the block appears
    before they fill in the amount rather than after they hit save.
    """
    inst = get_institution(db, slug)
    assert_same_institution(user, inst)
    fund = get_fund(db, inst, body.fund_code)
    verdict = ledger.check_spend(fund, body.purpose)
    return {
        "allowed": verdict.allowed,
        "code": verdict.code,
        "message_en": verdict.message_en,
        "message_ms": verdict.message_ms,
        "suggested_funds": [f.value for f in verdict.suggested_funds],
    }


@app.post("/i/{slug}/donations", status_code=201)
def create_donation(
    slug: str,
    body: DonationIn,
    db: Session = Depends(get_db),
    user: User = Depends(treasurer_or_admin),
):
    inst = get_institution(db, slug)
    assert_same_institution(user, inst)
    fund = get_fund(db, inst, body.fund_code)
    txn = ledger.record_donation(
        db,
        institution_id=inst.id,
        fund_id=fund.id,
        cash_fund_id=cash_fund(db, inst).id,
        amount=body.amount,
        description=body.description,
        channel=body.channel,
        recorded_by=user.id,
    )
    db.commit()
    return {
        "reference": txn.reference,
        "fund": fund.code,
        "amount": str(ledger.money(body.amount)),
        "balance": str(ledger.fund_balance(db, fund.id)),
    }


@app.post("/i/{slug}/disbursements", status_code=201)
def create_disbursement(
    slug: str,
    body: DisbursementIn,
    db: Session = Depends(get_db),
    user: User = Depends(treasurer_or_admin),
):
    """Spend money. This is the endpoint that can refuse."""
    inst = get_institution(db, slug)
    assert_same_institution(user, inst)
    fund = get_fund(db, inst, body.fund_code)

    balance = ledger.fund_balance(db, fund.id)
    if balance < ledger.money(body.amount):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "insufficient_fund_balance",
                "message_en": f"{fund.label_en} holds only {balance}.",
                "message_ms": f"Baki {fund.label_ms} hanya {balance}.",
            },
        )

    try:
        txn = ledger.record_disbursement(
            db,
            institution_id=inst.id,
            fund_id=fund.id,
            cash_fund_id=cash_fund(db, inst).id,
            amount=body.amount,
            purpose=body.purpose,
            description=body.description,
            recorded_by=user.id,
        )
    except FundRuleViolation as exc:
        db.rollback()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": exc.verdict.code,
                "message_en": exc.verdict.message_en,
                "message_ms": exc.verdict.message_ms,
                "suggested_funds": [f.value for f in exc.verdict.suggested_funds],
            },
        )
    except LedgerError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))

    db.commit()
    return {
        "reference": txn.reference,
        "fund": fund.code,
        "balance": str(ledger.fund_balance(db, fund.id)),
    }


@app.post("/devices/events", status_code=202)
def ingest_device_event(
    body: DeviceEventIn,
    x_device_signature: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """Signed events from a sensor box.

    Lands as PROVISIONAL. The box has an opinion about what went in; it does
    not get to assert a balance. Only a witnessed count promotes it.
    """
    device = db.scalars(select(Device).where(Device.serial == body.serial)).first()
    if device is None or not device.active:
        raise HTTPException(404, "Unknown or inactive device")

    signed = f"{body.serial}:{body.event_type}:{body.estimated_amount or ''}"
    expected = hmac.new(
        device.shared_secret.encode(), signed.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, x_device_signature):
        raise HTTPException(401, "Bad device signature")

    device.last_seen_at = datetime.now(timezone.utc)

    if body.event_type != "insertion" or body.estimated_amount is None:
        db.commit()
        return {"accepted": True, "recorded": False, "note": body.event_type}

    if device.default_fund_id is None:
        raise HTTPException(409, "Device has no default fund configured")

    txn = ledger.record_donation(
        db,
        institution_id=device.institution_id,
        fund_id=device.default_fund_id,
        cash_fund_id=get_fund(
            db, db.get(Institution, device.institution_id), "cash"
        ).id,
        amount=body.estimated_amount,
        description=f"Sensor insertion, {device.label}",
        channel=Channel.DEVICE,
        status=TxnStatus.PROVISIONAL,
        device_id=device.id,
        confidence=body.confidence,
    )
    db.commit()
    return {"accepted": True, "recorded": True, "reference": txn.reference}


@app.post("/devices/telemetry", status_code=202)
async def ingest_telemetry(
    body: TelemetryIn,
    x_device_signature: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """Live sensor events from a box: door, vibration, heartbeat.

    Signed like alerts: HMAC over serial, kind and the SHA-256 of the photo.
    The box reports what it saw. The server decides what it means.
    """
    if body.kind not in telemetry.EVENT_KINDS:
        raise HTTPException(422, f"Unknown event kind '{body.kind}'")
    device = db.scalars(select(Device).where(Device.serial == body.serial)).first()
    if device is None or not device.active:
        raise HTTPException(404, "Unknown or inactive device")

    expected = alert_service.device_signature(
        body.serial, body.kind, body.photo_sha256, device.shared_secret
    )
    if not hmac.compare_digest(expected, x_device_signature):
        raise HTTPException(401, "Bad device signature")

    photo = None
    if body.photo_b64:
        if not body.photo_sha256:
            raise HTTPException(422, "photo_sha256 is required with a photo")
        try:
            photo = alert_service.decode_photo(body.photo_b64)
        except alert_service.AlertError as exc:
            raise HTTPException(422, str(exc))

    try:
        result = telemetry.handle(
            db,
            device,
            kind=body.kind,
            detail=body.detail,
            pulses=body.pulses,
            photo=photo,
            photo_sha256=body.photo_sha256,
            signature=x_device_signature,
            occurred_at=body.occurred_at,
        )
    except alert_service.AlertError as exc:
        db.rollback()
        raise HTTPException(422, str(exc))
    db.commit()

    inst_id = device.institution_id
    if result["event"] is not None:
        await hub.broadcast(
            inst_id, telemetry.event_message(db, result["event"], result["alert"])
        )
    if result["alert"] is not None:
        await hub.broadcast(inst_id, {"type": "alert_count", **alert_service.open_counts(db, inst_id)})
    await hub.broadcast(inst_id, {"type": "status", "device": telemetry.device_status(device)})
    return {"accepted": True, "alert_id": result["alert"].id if result["alert"] else None}


_ALERT_COPY = {
    AlertKind.UNEXPLAINED_WEIGHT: (
        AlertSeverity.WARNING,
        "Unexplained weight in the tray",
    ),
    AlertKind.TRAY_EMPTIED: (AlertSeverity.INFO, "Tray was emptied"),
    AlertKind.TAMPER_SUSPECTED: (AlertSeverity.CRITICAL, "Possible tampering with the box"),
}


@app.post("/devices/alerts", status_code=201)
async def ingest_device_alert(
    body: DeviceAlertIn,
    x_device_signature: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """Signed alert from a box, optionally with a photo.

    The signature covers the SHA-256 of the photo, so the picture is bound to
    the box that claims to have taken it.
    """
    device = db.scalars(select(Device).where(Device.serial == body.serial)).first()
    if device is None or not device.active:
        raise HTTPException(404, "Unknown or inactive device")
    if body.kind not in _ALERT_COPY:
        raise HTTPException(422, "A box cannot raise this kind of alert")

    expected = alert_service.device_signature(
        body.serial, body.kind.value, body.photo_sha256, device.shared_secret
    )
    if not hmac.compare_digest(expected, x_device_signature):
        raise HTTPException(401, "Bad device signature")

    photo = None
    if body.photo_b64:
        if not body.photo_sha256:
            raise HTTPException(422, "photo_sha256 is required with a photo")
        try:
            photo = alert_service.decode_photo(body.photo_b64)
            severity, title = _ALERT_COPY[body.kind]
            alert = alert_service.create_alert(
                db,
                institution_id=device.institution_id,
                kind=body.kind,
                severity=severity,
                title=title,
                detail=body.detail,
                device=device,
                photo=photo,
                claimed_sha256=body.photo_sha256,
                signature=x_device_signature,
                occurred_at=body.occurred_at,
            )
        except alert_service.AlertError as exc:
            db.rollback()
            raise HTTPException(422, str(exc))
    else:
        severity, title = _ALERT_COPY[body.kind]
        alert = alert_service.create_alert(
            db,
            institution_id=device.institution_id,
            kind=body.kind,
            severity=severity,
            title=title,
            detail=body.detail,
            device=device,
            signature=x_device_signature,
            occurred_at=body.occurred_at,
        )

    device.last_seen_at = datetime.now(timezone.utc)
    event = telemetry._log(db, device, body.kind.value, alert.detail, alert, alert.occurred_at)
    db.commit()
    await hub.broadcast(device.institution_id, telemetry.event_message(db, event, alert))
    await hub.broadcast(
        device.institution_id,
        {"type": "alert_count", **alert_service.open_counts(db, device.institution_id)},
    )
    return {"accepted": True, "alert_id": alert.id, "seq": alert.seq}


class PushTokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    platform: str = Field(default="android", max_length=20)


@app.post("/i/{slug}/push-token", status_code=204)
def register_push_token(
    slug: str,
    body: PushTokenIn,
    db: Session = Depends(get_db),
    user: User = Depends(any_member),
):
    """Remember this phone so the box's alerts can reach it when the app is closed."""
    inst = get_institution(db, slug)
    assert_same_institution(user, inst)
    row = db.scalars(select(PushToken).where(PushToken.token == body.token)).first()
    if row is None:
        db.add(
            PushToken(
                user_id=user.id,
                institution_id=inst.id,
                token=body.token,
                platform=body.platform,
            )
        )
    else:  # same phone, maybe a different person signed in
        row.user_id, row.institution_id, row.platform = user.id, inst.id, body.platform
    db.commit()


@app.delete("/i/{slug}/push-token", status_code=204)
def remove_push_token(
    slug: str,
    token: str,
    db: Session = Depends(get_db),
    user: User = Depends(any_member),
):
    """Called on sign out so a shared phone stops receiving this person's alerts."""
    inst = get_institution(db, slug)
    assert_same_institution(user, inst)
    db.execute(
        delete(PushToken).where(PushToken.token == token, PushToken.user_id == user.id)
    )
    db.commit()


@app.post("/i/{slug}/counts", status_code=201)
def close_count(
    slug: str,
    body: CountIn,
    db: Session = Depends(get_db),
    user: User = Depends(treasurer_or_admin),
):
    inst = get_institution(db, slug)
    assert_same_institution(user, inst)
    device_id = None
    if body.device_serial:
        device = db.scalars(
            select(Device).where(
                Device.institution_id == inst.id, Device.serial == body.device_serial
            )
        ).first()
        if device is None:
            raise HTTPException(404, "Unknown device")
        device_id = device.id

    session_row = ledger.close_count(
        db,
        institution_id=inst.id,
        device_id=device_id,
        counted_total=body.counted_total,
        cash_fund_id=cash_fund(db, inst).id,
        witness_count=body.witness_count,
        note=body.note,
        closed_by=user.id,
    )
    raised = None
    if session_row.variance != Decimal("0"):
        adjustment = db.scalars(
            select(Transaction)
            .where(
                Transaction.institution_id == inst.id,
                Transaction.description.like("Count adjustment%"),
            )
            .order_by(Transaction.seq.desc())
            .limit(1)
        ).first()
        device_row = db.get(Device, device_id) if device_id else None
        raised = alert_service.variance_alert(
            db,
            institution_id=inst.id,
            device=device_row,
            session_row=session_row,
            adjustment_ref=adjustment.reference if adjustment else None,
        )
    db.commit()
    return {
        "alert_raised": raised is not None,
        "counted": str(session_row.counted_total),
        "estimated": str(session_row.estimated_total),
        "variance": str(session_row.variance),
        "flagged": abs(session_row.variance) > Decimal("20.00"),
    }


@app.get("/i/{slug}/transactions", response_model=list[TxnOut])
def list_transactions(
    slug: str,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    inst = get_institution(db, slug)
    assert_same_institution(user, inst)
    txns = db.scalars(
        select(Transaction)
        .where(Transaction.institution_id == inst.id)
        .order_by(Transaction.seq.desc())
        .limit(limit)
    ).all()
    return [
        TxnOut(
            reference=t.reference,
            description=t.description,
            occurred_at=t.occurred_at,
            channel=t.channel,
            status=t.status,
            amount=_txn_amount(t),
        )
        for t in txns
    ]


# ------------------------------------------------------------------ alerts


class AlertOut(BaseModel):
    id: str
    seq: int
    kind: AlertKind
    severity: AlertSeverity
    status: AlertStatus
    title: str
    detail: str
    device_label: str | None
    related_reference: str | None
    occurred_at: datetime
    has_photo: bool
    photo_sha256: str | None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None


def _alert_out(db: Session, a: Alert) -> AlertOut:
    device = db.get(Device, a.device_id) if a.device_id else None
    reviewer = db.get(User, a.reviewed_by) if a.reviewed_by else None
    return AlertOut(
        id=a.id,
        seq=a.seq,
        kind=a.kind,
        severity=a.severity,
        status=a.status,
        title=a.title,
        detail=a.detail,
        device_label=device.label if device else None,
        related_reference=a.related_reference,
        occurred_at=a.occurred_at,
        has_photo=a.photo is not None,
        photo_sha256=a.photo_sha256,
        reviewed_by=reviewer.name if reviewer else None,
        reviewed_at=a.reviewed_at,
        review_note=a.review_note,
    )


def _get_alert(db: Session, inst: Institution, alert_id: str) -> Alert:
    alert = db.get(Alert, alert_id)
    if alert is None or alert.institution_id != inst.id:
        raise HTTPException(404, "No such alert")
    return alert


@app.get("/i/{slug}/devices")
def list_devices(
    slug: str, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    inst = get_institution(db, slug)
    assert_same_institution(user, inst)
    rows = db.scalars(
        select(Device).where(Device.institution_id == inst.id, Device.active.is_(True))
    ).all()
    return [telemetry.device_status(d) for d in rows]


@app.get("/i/{slug}/live/events")
def recent_events(
    slug: str,
    limit: int = 40,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Catch-up for the live feed after the app was closed or reconnected."""
    inst = get_institution(db, slug)
    assert_same_institution(user, inst)
    rows = db.scalars(
        select(DeviceEvent)
        .where(DeviceEvent.institution_id == inst.id)
        .order_by(DeviceEvent.created_at.desc())
        .limit(min(limit, 100))
    ).all()
    out = []
    for ev in rows:
        alert = db.get(Alert, ev.alert_id) if ev.alert_id else None
        out.append(telemetry.event_message(db, ev, alert)["event"])
    return out


@app.websocket("/i/{slug}/live")
async def live_socket(websocket: WebSocket, slug: str, token: str = ""):
    """Push channel for the app's Live screen.

    Browsers and phones cannot set an Authorization header on a WebSocket, so
    the token rides in the query string. It is checked before the connection
    is accepted, and the institution comes from the token, not the URL.
    """
    db = SessionLocal()
    try:
        try:
            user = auth.get_current_user(token=token, db=db)
            inst = get_institution(db, slug)
            assert_same_institution(user, inst)
            inst_id = inst.id
        except HTTPException:
            await websocket.close(code=4401)
            return
    finally:
        db.close()

    await websocket.accept()
    hub.add(inst_id, websocket)
    try:
        await websocket.send_json({"type": "hello"})
        while True:
            # The app sends nothing meaningful; reading keeps the socket honest
            # and lets us notice when the phone goes away.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(inst_id, websocket)


@app.get("/i/{slug}/alerts/summary")
def alerts_summary(
    slug: str, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    inst = get_institution(db, slug)
    assert_same_institution(user, inst)
    return alert_service.open_counts(db, inst.id)


@app.get("/i/{slug}/alerts", response_model=list[AlertOut])
def list_alerts(
    slug: str,
    status_filter: AlertStatus | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    inst = get_institution(db, slug)
    assert_same_institution(user, inst)
    query = select(Alert).where(Alert.institution_id == inst.id)
    if status_filter is not None:
        query = query.where(Alert.status == status_filter)
    rows = db.scalars(query.order_by(Alert.seq.desc()).limit(limit)).all()
    return [_alert_out(db, a) for a in rows]


@app.get("/i/{slug}/alerts/{alert_id}", response_model=AlertOut)
def get_alert(
    slug: str,
    alert_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    inst = get_institution(db, slug)
    assert_same_institution(user, inst)
    return _alert_out(db, _get_alert(db, inst, alert_id))


@app.get("/i/{slug}/alerts/{alert_id}/photo")
def get_alert_photo(
    slug: str,
    alert_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    inst = get_institution(db, slug)
    assert_same_institution(user, inst)
    alert = _get_alert(db, inst, alert_id)
    if alert.photo is None:
        raise HTTPException(404, "This alert has no photo")
    return Response(
        content=alert.photo,
        media_type=alert.photo_mime or "image/jpeg",
        headers={"Cache-Control": "private, max-age=300"},
    )


@app.post("/i/{slug}/alerts/{alert_id}/verify")
def verify_alert(
    slug: str,
    alert_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Re-run every integrity check on this alert, live."""
    inst = get_institution(db, slug)
    assert_same_institution(user, inst)
    return alert_service.verify_alert(db, _get_alert(db, inst, alert_id))


@app.post("/i/{slug}/alerts/{alert_id}/review")
def review_alert(
    slug: str,
    alert_id: str,
    body: ReviewIn,
    db: Session = Depends(get_db),
    user: User = Depends(committee_reviewer),
):
    inst = get_institution(db, slug)
    assert_same_institution(user, inst)
    alert = _get_alert(db, inst, alert_id)
    try:
        alert_service.review(
            alert, action=body.action, note=body.note, user_id=user.id
        )
    except alert_service.AlertError as exc:
        raise HTTPException(422, str(exc))
    db.commit()
    return _alert_out(db, alert)


@app.get("/i/{slug}/transactions/{reference}/proof")
def transaction_proof(
    slug: str,
    reference: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """The seal behind one ledger entry, recomputed from the stored rows."""
    inst = get_institution(db, slug)
    assert_same_institution(user, inst)
    txn = db.scalars(
        select(Transaction).where(
            Transaction.institution_id == inst.id, Transaction.reference == reference
        )
    ).first()
    if txn is None:
        raise HTTPException(404, "No such transaction")
    seal = db.scalars(select(Seal).where(Seal.transaction_id == txn.id)).first()
    if seal is None:
        raise HTTPException(404, "Transaction has no seal")

    recomputed = sealing.hash_payload(sealing.transaction_payload(txn, txn.entries))
    prev = db.scalars(
        select(Seal).where(Seal.institution_id == inst.id, Seal.seq == seal.seq - 1)
    ).first()
    expected_prev = prev.chain_hash if prev else sealing.GENESIS_HASH
    recomputed_chain = sealing.chain(seal.prev_hash, recomputed, seal.seq)

    content_ok = recomputed == seal.payload_hash
    link_ok = seal.prev_hash == expected_prev
    chain_ok = recomputed_chain == seal.chain_hash
    return {
        "reference": txn.reference,
        "description": txn.description,
        "amount": str(_txn_amount(txn)),
        "seq": seal.seq,
        "sealed_at": seal.sealed_at,
        "payload_hash": seal.payload_hash,
        "recomputed_payload_hash": recomputed,
        "prev_hash": seal.prev_hash,
        "chain_hash": seal.chain_hash,
        "intact": content_ok and link_ok and chain_ok,
        "checks": [
            {
                "key": "content",
                "label": "Entry content matches its seal",
                "ok": content_ok,
                "detail": "Amounts, funds, time and description recompute to the seal"
                if content_ok
                else "This entry was changed after it was sealed",
            },
            {
                "key": "link",
                "label": "Follows the previous entry",
                "ok": link_ok,
                "detail": "Points at the seal before it"
                if link_ok
                else "Does not follow the previous seal",
            },
            {
                "key": "chain",
                "label": "Seal hash recomputes",
                "ok": chain_ok,
                "detail": "SHA-256(prev : payload : seq) matches"
                if chain_ok
                else "Seal hash was altered",
            },
        ],
    }


@app.get("/public/{slug}")
def public_page(slug: str, db: Session = Depends(get_db)):
    """What a donor sees after scanning the QR on the box."""
    inst = get_institution(db, slug)
    if not inst.public_page_enabled:
        raise HTTPException(404, "This institution has not published its ledger")

    result = ledger.verify(db, inst.id)
    funds = db.scalars(
        select(Fund).where(Fund.institution_id == inst.id, Fund.active.is_(True))
    ).all()

    return {
        "institution": inst.name,
        "funds": [
            {
                "label_ms": f.label_ms,
                "balance": str(ledger.fund_balance(db, f.id)),
            }
            for f in funds
            if f.code != "cash"
        ],
        "verification": {
            "intact": result.intact,
            "entries_sealed": result.checked,
            "head_hash": result.head_hash,
            "summary": result.summary,
        },
    }


@app.get("/i/{slug}/verify")
def verify_ledger(
    slug: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    inst = get_institution(db, slug)
    assert_same_institution(user, inst)
    result = ledger.verify(db, inst.id)
    return {
        "intact": result.intact,
        "checked": result.checked,
        "broken_at_seq": result.broken_at_seq,
        "reason": result.reason,
        "head_hash": result.head_hash,
        "summary": result.summary,
    }
