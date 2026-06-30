"""Application settings loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
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
    api_port: int = 8000

    database_url: str = "sqlite+aiosqlite:///./data/nexbuy.db"
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 300

    yfinance_enabled: bool = True
    tradingview_enabled: bool = False
    tradingview_api_key: str = ""
    fred_api_key: str = ""
    alpha_vantage_api_key: str = ""
    news_api_key: str = ""

    market_timezone: str = "America/New_York"
    report_times: str = "08:30,11:30,15:00,17:30"

    agent_weights_auto_calibrate: bool = True
    max_concentration_pct: float = 25.0

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
