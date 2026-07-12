"""Alert service with deduplication (no spam policy)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AlertORM
from database.repositories.alert_repository import AlertRepository
from domain.entities import Alert
from services.push_notification_service import PushNotificationService
from utils.logging import get_logger

logger = get_logger(__name__)


class AlertService:
    def __init__(
        self,
        repo: AlertRepository,
        cooldown_hours: int = 24,
        push: PushNotificationService | None = None,
    ) -> None:
        self._repo = repo
        self._cooldown_hours = cooldown_hours
        self._push = push or PushNotificationService()
        self._session: AsyncSession = repo._session  # noqa: SLF001

    async def emit(self, alert: Alert) -> Alert | None:
        """Save alert only if no duplicate within cooldown window."""
        if await self._is_duplicate(alert):
            logger.info(
                "alert.suppressed",
                ticker=alert.ticker,
                type=alert.alert_type.value,
                reason="cooldown",
            )
            return None
        saved = await self._repo.save(alert)
        logger.info("alert.emitted", ticker=alert.ticker, type=alert.alert_type.value, severity=alert.severity.value)
        if self._push.any_channel_configured:
            push_result = await self._push.notify_alert(saved)
            logger.info("alert.push", ticker=saved.ticker, **push_result)
        return saved

    async def emit_batch(self, alerts: list[Alert]) -> list[Alert]:
        emitted: list[Alert] = []
        for alert in alerts:
            saved = await self.emit(alert)
            if saved:
                emitted.append(saved)
        return emitted

    async def list_active(self, limit: int = 50) -> list[Alert]:
        return await self._repo.list_unacknowledged(limit)

    async def acknowledge(self, alert_id: str) -> bool:
        result = await self._session.execute(select(AlertORM).where(AlertORM.id == alert_id))
        row = result.scalar_one_or_none()
        if not row:
            return False
        row.acknowledged = True
        await self._session.commit()
        return True

    async def _is_duplicate(self, alert: Alert) -> bool:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self._cooldown_hours)
        result = await self._session.execute(
            select(AlertORM).where(
                and_(
                    AlertORM.ticker == alert.ticker.upper(),
                    AlertORM.alert_type == alert.alert_type.value,
                    AlertORM.created_at >= cutoff,
                )
            )
        )
        return result.scalar_one_or_none() is not None
