"""Alert repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AlertORM
from domain.entities import Alert


class AlertRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, alert: Alert) -> Alert:
        orm = AlertORM(
            id=alert.id,
            ticker=alert.ticker,
            alert_type=alert.alert_type.value,
            severity=alert.severity.value,
            title=alert.title,
            description=alert.description,
            created_at=alert.created_at,
            acknowledged=alert.acknowledged,
        )
        self._session.add(orm)
        await self._session.commit()
        return alert

    async def list_unacknowledged(self, limit: int = 50) -> list[Alert]:
        result = await self._session.execute(
            select(AlertORM)
            .where(AlertORM.acknowledged.is_(False))
            .order_by(AlertORM.created_at.desc())
            .limit(limit)
        )
        from domain.enums import AlertSeverity, AlertType

        return [
            Alert(
                id=r.id,
                ticker=r.ticker,
                alert_type=AlertType(r.alert_type),
                severity=AlertSeverity(r.severity),
                title=r.title,
                description=r.description,
                created_at=r.created_at,
                acknowledged=r.acknowledged,
            )
            for r in result.scalars().all()
        ]
