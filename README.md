---
title: NexBuy CEO Dashboard
emoji: 📊
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 8000
pinned: false
---

# NexBuy Investment Committee AI

Professional multi-agent investment research, portfolio management, and thesis generation platform.

## Architecture

- **Clean Architecture** with domain, services, providers, and agents layers
- **14 specialized agents** delivering structured evidence (never buy/sell decisions)
- **Investment Director** consolidates all reports into auditable investment theses
- **FastAPI** REST API, **APScheduler** for automated market reports
- **SQLite** en producción (Railway). Sin PostgreSQL ni Redis en el despliegue online.

## Fase 2 — Integraciones de datos

### Fase 2.3 — Sentimiento social (implementado)
Sin Reddit OAuth — usa fuentes públicas:

| Fuente | Método |
|--------|--------|
| **Stocktwits** | API pública por ticker |
| **Reddit** | Búsqueda `site:reddit.com` (DDG) |
| **Seeking Alpha** | Búsqueda `site:seekingalpha.com` |
| **Yahoo Finance** | Búsqueda `site:finance.yahoo.com` |

Cuando Reddit apruebe tu app OAuth, se conecta sin rehacer el agente.

Cuando Reddit apruebe tu app OAuth, se conecta sin rehacer el agente.

### Fase 3 — Automatización (implementado)
- **Watchlist Engine**: monitoreo automático cada 30 min (horario de mercado)
- **Alert Engine**: deduplicación 24h, sin spam
- **Market Monitor**: reportes 08:30 / 11:30 / 15:00 / 17:30 ET
- **Daily Investment Report**: generado a las 17:30 ET
- **Investment Memory**: evaluación automática a los 90 días + recalibración de pesos

```bash
python main.py serve      # API + scheduler integrado
python main.py scheduler  # Solo scheduler (standalone)
POST /api/v1/watchlist/scan  # Scan manual
GET  /api/v1/alerts
GET  /api/v1/reports/daily/latest
```

### Fase 2.2 — Market Data (implementado)
Cadena de fallback automática: **Polygon → Alpha Vantage → YFinance**

```bash
POLYGON_API_KEY=your_key
ALPHA_VANTAGE_API_KEY=your_key
POLYGON_PER_MINUTE_LIMIT=5
ALPHA_VANTAGE_DAILY_LIMIT=25
```

Si Polygon agota su cuota por minuto o falla, Alpha Vantage toma el relevo. Si Alpha Vantage agota sus 25 req/día, YFinance complementa.

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
docker build -t nexbuy-ceo .
docker run -p 8000:8000 -e DASHBOARD_ACCESS_TOKEN=Portafolio111 nexbuy-ceo
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

## Panel CEO online (celular + PC)

Flujo recomendado: **GitHub como fuente única** — escribes los cambios en Cursor (Cloud Agent), el código vive en el repo; no necesitas clonar en tu PC.

### Desplegar en Railway (recomendado — sin tarjeta)

[Railway](https://railway.com) permite registrarse **sin tarjeta de crédito**, con $5 de crédito de prueba (30 días) y luego **$1/mes gratis** para una app pequeña.

1. Entra en **[railway.com](https://railway.com)** → **Login** → **GitHub**
2. **New Project** → **Deploy from GitHub repo**
3. Selecciona `saorjuela14-lab/ACCIONESBUSQUEDA` (rama `main`)
4. Railway detecta el `Dockerfile` y construye solo
5. En el servicio → **Variables**, añade:

   | Variable | Valor |
   |----------|--------|
   | `DASHBOARD_ACCESS_TOKEN` | `Portafolio111` |
   | `DATABASE_URL` | `sqlite+aiosqlite:///./data/nexbuy.db` |
   | `REDIS_ENABLED` | `false` |
   | `APP_ENV` | `production` |
   | `SCHEDULER_ENABLED` | `true` |

6. **Settings** → **Networking** → **Generate Domain** (URL tipo `https://nexbuy-ceo.up.railway.app`)
7. Cada push a `main` redeploya automáticamente si activaste GitHub deploy

> **No añadas** PostgreSQL ni Redis en Railway — la app usa SQLite + memoria.

### Acceder al panel

| URL | Uso |
|-----|-----|
| `https://TU-APP.up.railway.app/login` | Pantalla de acceso |
| `https://TU-APP.up.railway.app/dashboard` | Panel CEO (PWA, móvil y escritorio) |

- Token de acceso: **`Portafolio111`**
- En el celular: abre `/login`, entra con el token y usa **Añadir a pantalla de inicio** (PWA).

### Cómo pedir cambios sin PC local

1. Escribe aquí en Cursor lo que quieres (watchlist, UI, nuevas fases).
2. El agente hace commit → push a `main` en GitHub.
3. Railway reconstruye y publica solo.

### Otras plataformas (por qué no usamos)

| Plataforma | Motivo |
|------------|--------|
| **Zeabur** | Desde mar 2026 **obliga a comprar servidor** (AWS/Hetzner…) — ya no hay cluster gratis |
| Render | Pide tarjeta de crédito |
| Hugging Face Docker | Requiere plan PRO |
| SnapDeploy | Pide add-ons PostgreSQL/Redis de pago |

## Disclaimer

For research and informational purposes only. Not financial advice.
