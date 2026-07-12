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
- **SQLite** en producción (FastAPI Cloud). Sin PostgreSQL ni Redis en el despliegue online.

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

### Desplegar en FastAPI Cloud (recomendado — gratis, sin tarjeta)

[FastAPI Cloud](https://fastapicloud.com) es la plataforma oficial del equipo FastAPI. Plan **Hobby gratis**, sin tarjeta, hasta 3 apps. Integración directa con GitHub.

#### Paso 1 — Crear cuenta (1 min)

1. Entra en **[fastapicloud.com](https://fastapicloud.com)** → **Start free** / **Sign up**
2. Inicia sesión con GitHub (recomendado)

#### Paso 2 — Conectar el repo (2 min)

> **Importante:** el proyecto usa **Python 3.12** (archivo `.python-version`). FastAPI Cloud no debe usar 3.14 — `pandas-ta`/`numba` no son compatibles.

1. Abre el [Dashboard](https://fastapicloud.com/dashboard)
2. En tu team → **Create App** → **From GitHub**
3. Instala la app **FastAPI Cloud** en GitHub si te lo pide
4. Selecciona el repo **`saorjuela14-lab/ACCIONESBUSQUEDA`**
5. Rama: **`main`** (deploy automático en cada push)
6. Pulsa **Create App** y espera el primer build (~5–10 min)

#### Paso 3 — Variables de entorno (1 min)

En el dashboard de tu app → **Environment Variables** → añade:

| Variable | Valor |
|----------|--------|
| `DASHBOARD_ACCESS_TOKEN` | `Portafolio111` |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/nexbuy.db` |
| `REDIS_ENABLED` | `false` |
| `APP_ENV` | `production` |
| `SCHEDULER_ENABLED` | `true` |

Marca `DASHBOARD_ACCESS_TOKEN` como **Secret** si la opción existe. Pulsa **Redeploy** tras guardar.

> **No añadas** PostgreSQL ni Redis — la app usa SQLite + caché en memoria.

#### Paso 4 — URL pública

Tras el deploy verás una URL tipo:

`https://nexbuy-ceo.fastapicloud.dev`

### Acceder al panel

| URL | Uso |
|-----|-----|
| `https://TU-APP.fastapicloud.dev/login` | Pantalla de acceso |
| `https://TU-APP.fastapicloud.dev/dashboard` | Panel CEO (PWA, móvil y escritorio) |

- Token de acceso: **`Portafolio111`**
- En el celular: abre `/login`, entra con el token y usa **Añadir a pantalla de inicio** (PWA).

### Cómo pedir cambios sin PC local

1. Escribe aquí en Cursor lo que quieres (watchlist, UI, nuevas fases).
2. El agente hace commit → push a `main` en GitHub.
3. FastAPI Cloud detecta el push y redeploya solo (integración GitHub).

### Deploy alternativo con GitHub Actions (opcional)

Si prefieres token en lugar de la app de GitHub:

1. Dashboard → tu app → **Deploy Tokens** → **Create Token**
2. Copia el token y el **App ID**
3. En GitHub → repo → **Settings → Secrets**:
   - `FASTAPI_CLOUD_TOKEN`
   - `FASTAPI_CLOUD_APP_ID`
4. El workflow `.github/workflows/fastapi-cloud-deploy.yml` desplegará en cada push a `main`

### Otras plataformas (referencia)

| Plataforma | Motivo |
|------------|--------|
| Railway | Crédito limitado tras trial |
| Zeabur | Obliga a comprar servidor |
| Render | Pide tarjeta |
| SnapDeploy | Pide add-ons PostgreSQL/Redis |

## Disclaimer

For research and informational purposes only. Not financial advice.
