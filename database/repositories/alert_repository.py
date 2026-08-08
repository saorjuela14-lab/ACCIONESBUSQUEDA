"""Alert repository."""

from sqlalchemy import func, select
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

    def _to_entity(self, r: AlertORM) -> Alert:
        from domain.enums import AlertSeverity, AlertType

        return Alert(
            id=r.id,
            ticker=r.ticker,
            alert_type=AlertType(r.alert_type),
            severity=AlertSeverity(r.severity),
            title=r.title,
            description=r.description,
            created_at=r.created_at,
            acknowledged=r.acknowledged,
        )

    async def list_unacknowledged(self, limit: int = 50, offset: int = 0) -> list[Alert]:
        result = await self._session.execute(
            select(AlertORM)
            .where(AlertORM.acknowledged.is_(False))
            .order_by(AlertORM.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return [self._to_entity(r) for r in result.scalars().all()]

    async def count_unacknowledged(self) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(AlertORM).where(AlertORM.acknowledged.is_(False))
        )
        return int(result.scalar() or 0)

    async def list_unacknowledged_page(
        self, *, limit: int = 25, offset: int = 0
    ) -> tuple[list[Alert], int]:
        total = await self.count_unacknowledged()
        items = await self.list_unacknowledged(limit=limit, offset=offset)
        return items, total
