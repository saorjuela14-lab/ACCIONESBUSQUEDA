# NexBuy Investment Committee AI

Professional multi-agent investment research, portfolio management, and thesis generation platform.

## Architecture

- **Clean Architecture** with domain, services, providers, and agents layers
- **14 specialized agents** delivering structured evidence (never buy/sell decisions)
- **Investment Director** consolidates all reports into auditable investment theses
- **FastAPI** REST API, **APScheduler** for automated market reports
- **SQLite + Redis** (production-ready for PostgreSQL/Redis Cloud)

## Fase 2 — Integraciones de datos

### FRED (implementado)
Con `FRED_API_KEY` en `.env`, el `macro_agent` consume datos verificados:
- Fed Funds Rate, CPI (YoY), Unemployment, GDP
- Yield curve (10Y-2Y), M2, Industrial Production
- Calendario económico (CPI, Employment, FOMC, GDP)

```bash
# .env
FRED_API_KEY=your_key_here
```

Registro gratuito: https://fred.stlouisfed.org/docs/api/api_key.html

## Quick Start

```bash
cp .env.example .env
pip install -e ".[dev]"
python main.py serve
```

API: http://localhost:8000/docs

## CLI

```bash
python main.py analyze AAPL
python main.py scheduler
```

## Docker

```bash
docker compose up -d
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| POST | `/api/v1/analyze` | Full committee analysis |
| GET | `/api/v1/watchlist` | List watchlist |
| POST | `/api/v1/watchlist` | Add ticker to watchlist |
| GET | `/api/v1/portfolios` | List portfolios |
| POST | `/api/v1/portfolios` | Create portfolio |

## Agent Pipeline

```
Evidence Agents → Alert Engine → Investment Director → Investment Memory
(fundamental, technical, macro, news, sentiment, valuation,
 country_risk, company_risk, corporate_actions, portfolio, watchlist)
```

## Disclaimer

For research and informational purposes only. Not financial advice.
