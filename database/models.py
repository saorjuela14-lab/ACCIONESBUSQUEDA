"""SQLAlchemy ORM models."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WatchlistORM(Base):
    __tablename__ = "watchlist"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PortfolioORM(Base):
    __tablename__ = "portfolios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    strategy: Mapped[str] = mapped_column(String(64))
    initial_capital: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    mode: Mapped[str] = mapped_column(String(16), default="real")
    positions_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class InvestmentMemoryORM(Base):
    __tablename__ = "investment_memory"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    thesis: Mapped[str] = mapped_column(Text)
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    scores_json: Mapped[str] = mapped_column(Text, default="{}")
    confidence: Mapped[float] = mapped_column(Float)
    scenario: Mapped[str] = mapped_column(Text)
    expected_outcome: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[str] = mapped_column(String(32))
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    was_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    evaluation_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)


class AgentWeightORM(Base):
    __tablename__ = "agent_weights"

    agent_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    accuracy: Mapped[float] = mapped_column(Float, default=0.5)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AlertORM(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    alert_type: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)


class WatchlistSnapshotORM(Base):
    __tablename__ = "watchlist_snapshots"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MarketReportORM(Base):
    __tablename__ = "market_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session: Mapped[str] = mapped_column(String(32))
    report_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DailyReportORM(Base):
    __tablename__ = "daily_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    report_date: Mapped[str] = mapped_column(String(10), index=True)
    report_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DailyTradeReportORM(Base):
    __tablename__ = "daily_trade_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    report_date: Mapped[str] = mapped_column(String(10), index=True)
    session: Mapped[str] = mapped_column(String(32), default="pre_market")
    report_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SentimentHistoryORM(Base):
    __tablename__ = "sentiment_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    aggregated_score: Mapped[float] = mapped_column(Float)
    label: Mapped[str] = mapped_column(String(16))
    retail_score: Mapped[float] = mapped_column(Float, default=0.0)
    news_score: Mapped[float] = mapped_column(Float, default=0.0)
    institutional_score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class PortfolioSnapshotORM(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(String(36), index=True)
    total_value: Mapped[float] = mapped_column(Float)
    return_pct: Mapped[float] = mapped_column(Float, default=0.0)
    cash: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class AuditEventORM(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    symbol: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    paper: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    message: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")


class OpsFlagORM(Base):
    """Key/value ops flags (kill switch, etc.)."""

    __tablename__ = "ops_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class OrganizationORM(Base):
    """B2B client tenant (access request → mesa approval → read-only monitor)."""

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    # False = pending mesa authorization (cannot login until approved)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Company WhatsApp inbox (E.164 digits / +prefix). Optional CallMeBot key.
    notify_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notify_whatsapp_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Latest deposit/withdrawal mirror (detail lives in capital_requests)
    deposit_status: Mapped[str] = mapped_column(String(24), default="none")  # none|requested|client_confirmed|received
    deposit_requested_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    deposit_note: Mapped[str | None] = mapped_column(String(280), nullable=True)
    withdrawal_status: Mapped[str] = mapped_column(String(24), default="none")  # none|requested|approved|rejected|paid
    withdrawal_requested_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    withdrawal_note: Mapped[str | None] = mapped_column(String(280), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class UserORM(Base):
    """Company or desk user (email/password)."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(160), default="")
    role: Mapped[str] = mapped_column(String(32), default="company_admin")  # desk | company_admin | viewer
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Personal WhatsApp for alert delivery (panel setting).
    notify_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notify_whatsapp_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthSessionORM(Base):
    """Opaque bearer session for company users."""

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class PasswordResetORM(Base):
    """One-time password recovery codes for company users (mesa-relayed)."""

    __tablename__ = "password_resets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    # Plain code kept until used/expired so the mesa can relay it (no SMTP yet)
    code_plain: Mapped[str | None] = mapped_column(String(12), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CapitalRequestORM(Base):
    """Client deposit/withdrawal requests against the shared firm Alpaca book."""

    __tablename__ = "capital_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    # deposit | withdrawal
    kind: Mapped[str] = mapped_column(String(16), index=True)
    amount_usd: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str | None] = mapped_column(String(280), nullable=True)
    # deposit: requested | client_confirmed | received | rejected
    # withdrawal: requested | approved | rejected | paid
    status: Mapped[str] = mapped_column(String(24), default="requested", index=True)
    desk_note: Mapped[str | None] = mapped_column(String(280), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PositionMandateORM(Base):
    __tablename__ = "position_mandates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    qty: Mapped[float] = mapped_column(Float, default=0.0)
    entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    trailing_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    peak_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_stop_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    thesis_invalidated: Mapped[bool] = mapped_column(Boolean, default=False)
    invalidate_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    beta: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mandate_json: Mapped[str] = mapped_column(Text, default="{}")


class TradeJournalORM(Base):
    """Durable open→close trade log for desk transparency and win-rate."""

    __tablename__ = "trade_journal"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    qty: Mapped[float] = mapped_column(Float, default=0.0)
    entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    r_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)
    thesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_tag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    mandate_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
