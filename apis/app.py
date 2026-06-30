"""FastAPI routes and application factory."""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apis.routes import analysis, health, portfolio, watchlist
from config.settings import get_settings
from database.engine import get_session, init_db
from orchestration.container import Container, bootstrap
from utils.logging import configure_logging, get_logger

logger = get_logger(__name__)
container = Container()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    await init_db()
    settings = get_settings()
    logger.info("app.startup", env=settings.app_env)
    yield
    logger.info("app.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description="Professional multi-agent investment committee platform",
        lifespan=lifespan,
    )

    app.include_router(health.router, tags=["health"])
    app.include_router(analysis.router, prefix="/api/v1", tags=["analysis"])
    app.include_router(watchlist.router, prefix="/api/v1", tags=["watchlist"])
    app.include_router(portfolio.router, prefix="/api/v1", tags=["portfolio"])

    return app


async def get_db_session() -> AsyncSession:
    async for session in get_session():
        yield session
