"""Application settings loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the investment committee platform."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "NexBuy Investment Committee"
    app_env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, validation_alias=AliasChoices("API_PORT", "PORT"))

    database_url: str = "sqlite+aiosqlite:///./data/nexbuy.db"
    redis_enabled: bool = False
    redis_url: str = ""
    cache_ttl_seconds: int = 300

    yfinance_enabled: bool = True
    tradingview_enabled: bool = False
    tradingview_api_key: str = ""
    fred_api_key: str = ""
    polygon_api_key: str = ""
    polygon_api_base_url: str = "https://api.massive.com"
    alpha_vantage_api_key: str = ""
    news_api_key: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    dashboard_access_token: str = ""
    public_base_url: str = ""

    # Provider rate limits (free tiers)
    polygon_daily_limit: int = 1000
    polygon_per_minute_limit: int = 5
    alpha_vantage_daily_limit: int = 25

    market_timezone: str = "America/New_York"
    report_times: str = "08:30,11:30,15:00,17:30"

    agent_weights_auto_calibrate: bool = True
    max_concentration_pct: float = 25.0

    scheduler_enabled: bool = True
    watchlist_scan_interval_minutes: int = 30
    daily_trade_sessions: str = "08:30,11:30"
    memory_evaluation_days: int = 90
    alert_cooldown_hours: int = 24

    # Push notifications (optional)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_alerts_enabled: bool = True
    alert_webhook_url: str = ""
    push_daily_trades: bool = True

    # WhatsApp status / briefing (CallMeBot | Meta Cloud API | Twilio)
    whatsapp_enabled: bool = True
    whatsapp_provider: str = "auto"  # auto | callmebot | meta | twilio
    whatsapp_phone: str = ""  # CallMeBot: digits e.g. 573001234567
    whatsapp_api_key: str = ""  # CallMeBot apikey
    whatsapp_token: str = ""  # Meta permanent access token
    whatsapp_phone_number_id: str = ""  # Meta phone number id
    whatsapp_to: str = ""  # Recipient E.164 for Meta/Twilio e.g. 573001234567
    whatsapp_api_version: str = "v21.0"
    whatsapp_template_name: str = ""  # optional Meta template for proactive msgs
    whatsapp_template_lang: str = "es"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""  # e.g. whatsapp:+14155238886
    whatsapp_briefing_enabled: bool = True
    # Exactly 3 desk messages per trading day (ET): open, lunch, close
    whatsapp_briefing_times: str = "09:35,12:30,16:05"

    # Alpaca Trading API — LIVE by default (https://docs.alpaca.markets/)
    # Compatible with https://github.com/alpacahq/cli env vars
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = False
    # CLI-compatible: ALPACA_LIVE_TRADE=true → live (overrides alpaca_paper when set)
    alpaca_live_trade: bool | None = None
    alpaca_base_url: str = ""  # override; empty → api.alpaca.markets (live) or paper-api
    alpaca_data_base_url: str = "https://data.alpaca.markets"
    alpaca_data_feed: str = "iex"  # iex (free) | sip (paid) | delayed_sip

    http_max_retries: int = 3
    http_retry_backoff: float = 1.5

    # Risk desk (hard gates on buys + recommendation sizing)
    risk_max_position_pct: float = 35.0
    risk_max_sector_pct: float = 40.0
    risk_max_gross_exposure_pct: float = 90.0
    risk_cash_reserve_pct: float = 10.0
    risk_max_daily_loss_pct: float = 5.0
    risk_max_open_positions: int = 8
    risk_require_stop_loss: bool = True
    risk_min_reward_risk: float = 1.2
    risk_off_size_mult: float = 0.35
    risk_crisis_block_buys: bool = True
    # Firm autonomy — independent capital desk (buys/exits without human click).
    # Kill switch + committee unanimity + risk desk remain hard gates.
    firm_autonomy: bool = True
    auto_execute_trades: bool = True
    auto_execute_live: bool = True  # LIVE auto-submit authorized
    auto_execute_max_notional: float = 25.0
    auto_execute_require_market_open: bool = True
    auto_execute_paper_first: bool = False  # skip paper soak when firm_autonomy
    autopilot_interval_minutes: int = 10  # scheduled full desk loop (0 = off)

    # Lifecycle desk
    lifecycle_enabled: bool = True
    lifecycle_scan_interval_minutes: int = 15
    lifecycle_trailing_pct: float = 0.08
    lifecycle_time_stop_days: int = 10
    lifecycle_default_stop_pct: float = 0.08
    lifecycle_default_target_pct: float = 0.12
    lifecycle_auto_exit: bool = True

    # Continuous reconcile
    reconcile_interval_minutes: int = 20
    reconcile_auto_sync: bool = True

    # Hard portfolio risk gates
    risk_max_var_pct: float = 8.0
    risk_max_portfolio_beta: float = 1.8
    risk_enforce_sector_cap: bool = True
    risk_enforce_var_beta: bool = True

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_db_url(cls, value: str) -> str:
        from database.url import normalize_database_url

        return normalize_database_url(str(value or ""))

    @field_validator("report_times", mode="before")
    @classmethod
    def parse_report_times(cls, value: str | list[str]) -> str:
        if isinstance(value, list):
            return ",".join(value)
        return value

    @property
    def report_schedule(self) -> list[str]:
        return [t.strip() for t in self.report_times.split(",") if t.strip()]

    @property
    def daily_trade_schedule(self) -> list[str]:
        return [t.strip() for t in self.daily_trade_sessions.split(",") if t.strip()]

    @property
    def whatsapp_briefing_schedule(self) -> list[str]:
        return [t.strip() for t in self.whatsapp_briefing_times.split(",") if t.strip()]

    @property
    def effective_autopilot_interval_minutes(self) -> int:
        """Scheduled autopilot cadence; firm autonomy defaults to 10m when unset."""
        if self.autopilot_interval_minutes and self.autopilot_interval_minutes > 0:
            return int(self.autopilot_interval_minutes)
        if self.firm_autonomy:
            return 10
        return 0

    @property
    def effective_alpaca_paper(self) -> bool:
        """Paper vs LIVE. ALPACA_LIVE_TRADE (CLI) wins when set."""
        if self.alpaca_live_trade is not None:
            return not self.alpaca_live_trade
        return self.alpaca_paper


@lru_cache
def get_settings() -> Settings:
    return Settings()
