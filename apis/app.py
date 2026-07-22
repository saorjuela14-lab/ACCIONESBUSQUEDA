"""FastAPI routes and application factory."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from apis.middleware.access_auth import AccessTokenMiddleware
from apis.routes import alerts, allocation, analysis, auth, broker, correlations, dashboard, discovery, graph, health, market, ops, portfolio, proposal, providers, recommendations, reports, risk, sentiment, voice, watchlist
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

    scheduler = None
    if settings.scheduler_enabled:
        from services.scheduler_service import start_scheduler
        scheduler = await start_scheduler()
        logger.info("app.scheduler.started")

    yield

    if scheduler:
        scheduler.stop()
    logger.info("app.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description="Professional multi-agent investment committee platform",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AccessTokenMiddleware)

    dashboard_dir = Path(__file__).resolve().parent.parent / "dashboard"
    if dashboard_dir.exists():
        app.mount("/dashboard/static", StaticFiles(directory=dashboard_dir), name="dashboard-static")

        @app.get("/")
        async def root():
            return RedirectResponse(url="/dashboard")

        @app.get("/dashboard")
        async def dashboard_index():
            return FileResponse(dashboard_dir / "index.html")

        @app.get("/login")
        async def login_page():
            return FileResponse(dashboard_dir / "login.html")

    app.include_router(health.router, tags=["health"])
    app.include_router(auth.router, prefix="/api/v1", tags=["auth"])
    app.include_router(analysis.router, prefix="/api/v1", tags=["analysis"])
    app.include_router(watchlist.router, prefix="/api/v1", tags=["watchlist"])
    app.include_router(portfolio.router, prefix="/api/v1", tags=["portfolio"])
    app.include_router(alerts.router, prefix="/api/v1", tags=["alerts"])
    app.include_router(reports.router, prefix="/api/v1", tags=["reports"])
    app.include_router(providers.router, prefix="/api/v1", tags=["providers"])
    app.include_router(correlations.router, prefix="/api/v1", tags=["correlations"])
    app.include_router(dashboard.router, prefix="/api/v1", tags=["dashboard"])
    app.include_router(market.router, prefix="/api/v1", tags=["market"])
    app.include_router(graph.router, prefix="/api/v1", tags=["graph"])
    app.include_router(proposal.router, prefix="/api/v1", tags=["proposal"])
    app.include_router(allocation.router, prefix="/api/v1", tags=["allocation"])
    app.include_router(sentiment.router, prefix="/api/v1", tags=["sentiment"])
    app.include_router(discovery.router, prefix="/api/v1", tags=["discovery"])
    app.include_router(recommendations.router, prefix="/api/v1", tags=["recommendations"])
    app.include_router(broker.router, prefix="/api/v1", tags=["broker"])
    app.include_router(risk.router, prefix="/api/v1", tags=["risk"])
    app.include_router(ops.router, prefix="/api/v1", tags=["ops"])
    app.include_router(voice.router, prefix="/api/v1", tags=["voice"])

    return app


async def get_db_session() -> AsyncSession:
    async for session in get_session():
        yield session
