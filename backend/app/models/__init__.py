"""Database models for the AmanahTrack ledger."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.fiqh import FundCategory, PurposeCode


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class EntrySide(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class AccountNature(str, Enum):
    """Which direction increases this account.

    Cash is an asset: debits raise it. A restricted fund behaves like equity:
    credits raise it. Applying one convention to both is how you end up with a
    mosque that appears to hold negative money.
    """

    ASSET = "asset"
    FUND = "fund"


class TxnStatus(str, Enum):
    """A transaction's confidence level.

    PROVISIONAL is what a sensor box produces: something went into the box,
    we have an estimate, nobody has counted it yet. CONFIRMED is what a human
    attests to. VOID is a reversal; entries are never deleted.
    """

    PROVISIONAL = "provisional"
    CONFIRMED = "confirmed"
    VOID = "void"


class Channel(str, Enum):
    DEVICE = "device"
    TELEGRAM = "telegram"
    DASHBOARD = "dashboard"
    BANK_IMPORT = "bank_import"
    QR_PAYMENT = "qr_payment"


class Role(str, Enum):
    ADMIN = "admin"
    TREASURER = "treasurer"
    AUDITOR = "auditor"
    COMMITTEE = "committee"


class AlertKind(str, Enum):
    UNEXPLAINED_WEIGHT = "unexplained_weight"
    TRAY_EMPTIED = "tray_emptied"
    COUNT_VARIANCE = "count_variance"
    TAMPER_SUSPECTED = "tamper_suspected"
    CHAIN_BROKEN = "chain_broken"
    DOOR_OPENED = "door_opened"
    DOOR_LEFT_OPEN = "door_left_open"
    VIBRATION = "vibration"
    DEVICE_OFFLINE = "device_offline"


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    ESCALATED = "escalated"


class Institution(Base):
    __tablename__ = "institutions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    public_page_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    funds: Mapped[list["Fund"]] = relationship(back_populates="institution")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("institution_id", "telegram_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"))
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    telegram_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    role: Mapped[Role] = mapped_column(SAEnum(Role), default=Role.COMMITTEE)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PushToken(Base):
    """A phone that wants push notifications for one user.

    The token comes from Expo's push service. One user can have several phones,
    and the same phone token is unique, so signing in as someone else on the
    same phone moves the token to the new user.
    """

    __tablename__ = "push_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"))
    token: Mapped[str] = mapped_column(String(200), unique=True)
    platform: Mapped[str] = mapped_column(String(20), default="android")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Fund(Base):
    """An instance of a fund category held by one institution.

    An institution can hold several infaq funds at once, each bound to a
    different donor purpose, which is why category alone is not the key.
    """

    __tablename__ = "funds"
    __table_args__ = (
        UniqueConstraint("institution_id", "code"),
        Index("ix_fund_institution", "institution_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"))
    code: Mapped[str] = mapped_column(String(60))
    category: Mapped[FundCategory] = mapped_column(SAEnum(FundCategory))
    nature: Mapped[AccountNature] = mapped_column(
        SAEnum(AccountNature), default=AccountNature.FUND
    )
    label_ms: Mapped[str] = mapped_column(String(120))
    label_en: Mapped[str] = mapped_column(String(120))
    bound_purpose: Mapped[PurposeCode | None] = mapped_column(
        SAEnum(PurposeCode), nullable=True
    )
    donor_intent_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    institution: Mapped[Institution] = relationship(back_populates="funds")


class Device(Base):
    """A registered sensor box. Events must be signed with its key."""

    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("institution_id", "serial"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"))
    serial: Mapped[str] = mapped_column(String(60))
    label: Mapped[str] = mapped_column(String(120))
    default_fund_id: Mapped[str | None] = mapped_column(
        ForeignKey("funds.id"), nullable=True
    )
    shared_secret: Mapped[str] = mapped_column(String(128))
    firmware: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Live state, updated by telemetry. door_state is "open", "closed" or None
    # (never reported). The two flags stop the periodic check from raising the
    # same alert every 30 seconds.
    door_state: Mapped[str | None] = mapped_column(String(10), nullable=True)
    door_opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    door_alerted: Mapped[bool] = mapped_column(Boolean, default=False)
    offline_alerted: Mapped[bool] = mapped_column(Boolean, default=False)


class Transaction(Base):
    """One financial event. Balanced by its entries."""

    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_txn_institution_date", "institution_id", "occurred_at"),
        UniqueConstraint("institution_id", "seq"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"))
    seq: Mapped[int] = mapped_column(Integer)
    reference: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    channel: Mapped[Channel] = mapped_column(SAEnum(Channel))
    status: Mapped[TxnStatus] = mapped_column(
        SAEnum(TxnStatus), default=TxnStatus.CONFIRMED
    )
    purpose: Mapped[PurposeCode | None] = mapped_column(
        SAEnum(PurposeCode), nullable=True
    )
    confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3), nullable=True
    )
    device_id: Mapped[str | None] = mapped_column(
        ForeignKey("devices.id"), nullable=True
    )
    recorded_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    reverses_id: Mapped[str | None] = mapped_column(
        ForeignKey("transactions.id"), nullable=True
    )

    entries: Mapped[list["Entry"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )


class Entry(Base):
    """One leg of a transaction. Debits and credits must sum to zero."""

    __tablename__ = "entries"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_entry_amount_positive"),
        Index("ix_entry_fund", "fund_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"))
    fund_id: Mapped[str] = mapped_column(ForeignKey("funds.id"))
    side: Mapped[EntrySide] = mapped_column(SAEnum(EntrySide))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))

    transaction: Mapped[Transaction] = relationship(back_populates="entries")


class Seal(Base):
    """One link in the tamper-evident chain.

    Every committed transaction gets a seal whose hash covers the transaction
    content and the previous seal. Altering history breaks every seal after
    the edit, which is detectable by anyone holding an older root.
    """

    __tablename__ = "seals"
    __table_args__ = (
        UniqueConstraint("institution_id", "seq"),
        Index("ix_seal_txn", "transaction_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"))
    seq: Mapped[int] = mapped_column(Integer)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"))
    payload_hash: Mapped[str] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64))
    chain_hash: Mapped[str] = mapped_column(String(64))
    sealed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CountSession(Base):
    """A physical counting event that reconciles provisional device entries."""

    __tablename__ = "count_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"))
    device_id: Mapped[str | None] = mapped_column(
        ForeignKey("devices.id"), nullable=True
    )
    counted_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    estimated_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    variance: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    witness_count: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    counted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    closed_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )


class Alert(Base):
    """Something the committee should look at, with evidence attached.

    Alerts form their own hash chain, separate from the ledger. The seal covers
    what the alert says, which device raised it and the SHA-256 of the photo, so
    swapping the picture or editing the text afterwards is detectable. Status
    and review notes are deliberately outside the seal: they are the committee
    doing its job, not the evidence changing.
    """

    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("institution_id", "seq"),
        Index("ix_alert_institution_status", "institution_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"))
    seq: Mapped[int] = mapped_column(Integer)
    device_id: Mapped[str | None] = mapped_column(
        ForeignKey("devices.id"), nullable=True
    )
    kind: Mapped[AlertKind] = mapped_column(SAEnum(AlertKind))
    severity: Mapped[AlertSeverity] = mapped_column(SAEnum(AlertSeverity))
    status: Mapped[AlertStatus] = mapped_column(
        SAEnum(AlertStatus), default=AlertStatus.OPEN
    )
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text, default="")
    related_reference: Mapped[str | None] = mapped_column(String(40), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    photo: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    photo_mime: Mapped[str | None] = mapped_column(String(40), nullable=True)
    photo_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_signature: Mapped[str | None] = mapped_column(String(64), nullable=True)

    payload_hash: Mapped[str] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64))
    chain_hash: Mapped[str] = mapped_column(String(64))

    reviewed_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class DeviceEvent(Base):
    """One sensor reading worth showing in the live feed.

    Heartbeats are not stored (they would be 2,880 rows a day per box); they
    only refresh the device's last_seen_at. Everything else the box reports is
    kept here, linked to the alert it raised when it raised one.
    """

    __tablename__ = "device_events"
    __table_args__ = (Index("ix_device_event_inst_time", "institution_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"))
    device_id: Mapped[str | None] = mapped_column(
        ForeignKey("devices.id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(30))
    detail: Mapped[str] = mapped_column(Text, default="")
    alert_id: Mapped[str | None] = mapped_column(ForeignKey("alerts.id"), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
