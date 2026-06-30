"""Application entry point."""

import argparse
import asyncio

import uvicorn

from apis.app import create_app
from config.settings import get_settings
from utils.logging import configure_logging, get_logger

logger = get_logger(__name__)
app = create_app()


async def run_analysis(ticker: str) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession
    from database.engine import init_db, get_session
    from apis.routes.analysis import _build_analysis_service
    from reports.writer import ReportWriter

    configure_logging()
    await init_db()

    async for session in get_session():
        assert isinstance(session, AsyncSession)
        service = _build_analysis_service(session)
        thesis = await service.analyze_ticker(ticker)
        writer = ReportWriter()
        json_path = writer.write_thesis(thesis)
        md_path = writer._output_dir / f"thesis_{thesis.ticker}.md"
        md_path.write_text(writer.to_markdown_thesis(thesis), encoding="utf-8")
        logger.info("cli.analysis.complete", ticker=ticker, json=str(json_path), md=str(md_path))
        print(f"\nRecommendation: {thesis.recommendation.value.upper()}")
        print(f"Confidence: {thesis.confidence:.0%}")
        print(f"Report: {json_path}")
        break


async def run_scheduler() -> None:
    from services.scheduler_service import start_scheduler

    configure_logging()
    scheduler = await start_scheduler()
    logger.info("scheduler.running")
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        scheduler.stop()


def cli_main() -> None:
    parser = argparse.ArgumentParser(description="NexBuy Investment Committee AI")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="Start API server")
    analyze_parser = sub.add_parser("analyze", help="Run full committee analysis")
    analyze_parser.add_argument("ticker", help="Stock ticker symbol")

    sub.add_parser("scheduler", help="Start automated market report scheduler")

    args = parser.parse_args()
    settings = get_settings()

    if args.command == "analyze":
        asyncio.run(run_analysis(args.ticker.upper()))
    elif args.command == "scheduler":
        asyncio.run(run_scheduler())
    else:
        configure_logging()
        uvicorn.run(
            "main:app",
            host=settings.api_host,
            port=settings.api_port,
            reload=settings.app_env == "development",
        )


if __name__ == "__main__":
    cli_main()
