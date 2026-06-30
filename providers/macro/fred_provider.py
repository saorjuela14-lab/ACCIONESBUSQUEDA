"""FRED (Federal Reserve Economic Data) API provider."""

from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from config.settings import get_settings
from providers.interfaces import MacroProvider
from providers.macro.fred_series import FRED_RELEASE_IDS, FRED_SERIES
from utils.logging import get_logger
from utils.retry import async_retry

logger = get_logger(__name__)

FRED_BASE_URL = "https://api.stlouisfed.org/fred"


class FredProvider(MacroProvider):
    """Fetches verified US macroeconomic data from the St. Louis Fed FRED API."""

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.fred_api_key
        if not self._api_key:
            raise ValueError("FRED_API_KEY is required. Set it in .env or pass api_key.")

    @async_retry
    async def _get_observations(
        self,
        series_id: str,
        limit: int = 2,
        sort_order: str = "desc",
    ) -> list[dict[str, Any]]:
        params = {
            "series_id": series_id,
            "api_key": self._api_key,
            "file_type": "json",
            "sort_order": sort_order,
            "limit": limit,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{FRED_BASE_URL}/series/observations", params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("observations", [])

    def _parse_value(self, observation: dict[str, Any]) -> float | None:
        raw = observation.get("value", ".")
        if raw in (".", "", None):
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    def _calc_change_pct(self, current: float, previous: float | None) -> float | None:
        if previous is None or previous == 0:
            return None
        return round(((current - previous) / abs(previous)) * 100, 2)

    async def _fetch_series(self, key: str) -> dict[str, Any] | None:
        definition = FRED_SERIES.get(key)
        if not definition:
            return None

        observations = await self._get_observations(definition.series_id, limit=2)
        valid = [o for o in observations if self._parse_value(o) is not None]
        if not valid:
            return None

        current_val = self._parse_value(valid[0])
        previous_val = self._parse_value(valid[1]) if len(valid) > 1 else None
        if current_val is None:
            return None

        return {
            "key": key,
            "series_id": definition.series_id,
            "label": definition.label,
            "unit": definition.unit,
            "category": definition.category,
            "value": round(current_val, 4),
            "previous_value": round(previous_val, 4) if previous_val is not None else None,
            "change_pct": self._calc_change_pct(current_val, previous_val),
            "date": valid[0].get("date"),
            "source": "fred",
        }

    async def _fetch_cpi_yoy(self) -> dict[str, Any] | None:
        """Calculate year-over-year CPI inflation from monthly observations."""
        observations = await self._get_observations("CPIAUCSL", limit=14, sort_order="desc")
        valid = [(o, self._parse_value(o)) for o in observations]
        valid = [(o, v) for o, v in valid if v is not None]
        if len(valid) < 13:
            return None

        current = valid[0][1]
        year_ago = valid[12][1]
        yoy = self._calc_change_pct(current, year_ago)
        if yoy is None:
            return None

        return {
            "key": "CPI_YOY",
            "series_id": "CPIAUCSL",
            "label": "CPI Inflation (YoY)",
            "unit": "%",
            "category": "inflation",
            "value": yoy,
            "previous_value": None,
            "change_pct": None,
            "date": valid[0][0].get("date"),
            "source": "fred",
            "raw_cpi_index": round(current, 2),
        }

    async def get_macro_snapshot(self) -> dict[str, Any]:
        indicators: dict[str, Any] = {}
        references: list[dict[str, Any]] = []

        for key in FRED_SERIES:
            try:
                result = await self._fetch_series(key)
                if result:
                    indicators[key] = result
                    references.append(
                        {
                            "source": "fred",
                            "series_id": result["series_id"],
                            "data_point": key,
                            "value": result["value"],
                            "date": result["date"],
                            "url": f"https://fred.stlouisfed.org/series/{result['series_id']}",
                        }
                    )
            except Exception as exc:
                logger.warning("fred.series.failed", key=key, error=str(exc))

        try:
            cpi_yoy = await self._fetch_cpi_yoy()
            if cpi_yoy:
                indicators["CPI_YOY"] = cpi_yoy
                references.append(
                    {
                        "source": "fred",
                        "series_id": "CPIAUCSL",
                        "data_point": "CPI_YOY",
                        "value": cpi_yoy["value"],
                        "date": cpi_yoy["date"],
                    }
                )
        except Exception as exc:
            logger.warning("fred.cpi_yoy.failed", error=str(exc))

        return {
            "indicators": indicators,
            "references": references,
            "provider": "fred",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    @async_retry
    async def get_economic_calendar(self, days: int = 7) -> list[dict[str, Any]]:
        today = date.today()
        end = today + timedelta(days=days)
        params = {
            "api_key": self._api_key,
            "file_type": "json",
            "sort_order": "asc",
            "include_release_dates_with_no_data": "true",
            "realtime_start": today.isoformat(),
            "realtime_end": end.isoformat(),
            "limit": 50,
        }

        events: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            for release_id, release_name in FRED_RELEASE_IDS.items():
                release_params = {**params, "release_id": release_id}
                try:
                    response = await client.get(
                        f"{FRED_BASE_URL}/release/dates",
                        params=release_params,
                    )
                    response.raise_for_status()
                    for entry in response.json().get("release_dates", []):
                        events.append(
                            {
                                "event": release_name,
                                "date": entry.get("date"),
                                "release_id": release_id,
                                "impact": "high" if release_id in (10, 50, 101) else "medium",
                                "source": "fred",
                            }
                        )
                except Exception as exc:
                    logger.warning("fred.calendar.failed", release_id=release_id, error=str(exc))

        events.sort(key=lambda e: e.get("date", ""))
        return events[:15]
