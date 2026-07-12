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
    x_api_key: str = ""
    x_api_secret: str = ""
    x_bearer_token: str = ""
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

    http_max_retries: int = 3
    http_retry_backoff: float = 1.5

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
