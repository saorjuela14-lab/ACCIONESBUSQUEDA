"""DB row lease — PgBouncer transaction-mode safe (no session advisory locks)."""

from __future__ import annotations

import logging

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base
from services.db_lease import (
    ALERT_AFTER_MISSES,
    LEASE_CRYPTO_A,
    acquire_lease,
    force_expire_for_tests,
    heartbeat_lease,
    release_lease,
    snapshot_lease,
)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_two_replicas_only_one_wins(session: AsyncSession):
    a = await acquire_lease(session, name=LEASE_CRYPTO_A, owner="replica-a", ttl_seconds=120)
    b = await acquire_lease(session, name=LEASE_CRYPTO_A, owner="replica-b", ttl_seconds=120)
    assert a.acquired is True
    assert a.owner == "replica-a"
    assert b.acquired is False
    assert b.owner == "replica-a"
    assert b.misses_consecutive == 1


@pytest.mark.asyncio
async def test_expired_lease_taken_over(session: AsyncSession):
    first = await acquire_lease(session, name=LEASE_CRYPTO_A, owner="dead", ttl_seconds=120)
    assert first.acquired is True
    await force_expire_for_tests(session, LEASE_CRYPTO_A)
    second = await acquire_lease(session, name=LEASE_CRYPTO_A, owner="alive", ttl_seconds=120)
    assert second.acquired is True
    assert second.owner == "alive"


@pytest.mark.asyncio
async def test_heartbeat_extends_expiry(session: AsyncSession):
    got = await acquire_lease(session, name=LEASE_CRYPTO_A, owner="me", ttl_seconds=30)
    assert got.acquired is True
    first_exp = got.expires_at
    ok = await heartbeat_lease(session, name=LEASE_CRYPTO_A, owner="me", ttl_seconds=180)
    assert ok is True
    snap = await snapshot_lease(session, LEASE_CRYPTO_A)
    assert snap.expires_at != first_exp
    assert snap.owner == "me"


@pytest.mark.asyncio
async def test_release_on_exception_only_if_owner(session: AsyncSession):
    await acquire_lease(session, name=LEASE_CRYPTO_A, owner="me", ttl_seconds=120)
    try:
        raise RuntimeError("cycle_failed")
    except RuntimeError:
        released = await release_lease(session, name=LEASE_CRYPTO_A, owner="me")
        assert released is True
    snap = await snapshot_lease(session, LEASE_CRYPTO_A)
    assert snap.owner is None
    stolen = await acquire_lease(session, name=LEASE_CRYPTO_A, owner="other", ttl_seconds=120)
    assert stolen.acquired is True
    assert await release_lease(session, name=LEASE_CRYPTO_A, owner="me") is False
    still = await snapshot_lease(session, LEASE_CRYPTO_A)
    assert still.owner == "other"


@pytest.mark.asyncio
async def test_misses_alert_at_two(session: AsyncSession, caplog):
    await acquire_lease(session, name=LEASE_CRYPTO_A, owner="holder", ttl_seconds=120)
    with caplog.at_level(logging.WARNING, logger="nexbuy.db_lease"):
        miss1 = await acquire_lease(session, name=LEASE_CRYPTO_A, owner="challenger", ttl_seconds=120)
        miss2 = await acquire_lease(session, name=LEASE_CRYPTO_A, owner="challenger", ttl_seconds=120)
    assert miss1.acquired is False
    assert miss1.misses_consecutive == 1
    assert miss1.alert is False
    assert miss2.misses_consecutive >= ALERT_AFTER_MISSES
    assert miss2.alert is True
    assert any("lease_miss_alert" in r.getMessage() for r in caplog.records)
    winner = await acquire_lease(session, name=LEASE_CRYPTO_A, owner="holder", ttl_seconds=120)
    assert winner.acquired is True
    assert winner.misses_consecutive == 0
