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
- **SQLite + Redis** (production-ready for PostgreSQL/Redis Cloud)

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
docker compose up -d
```

Producción usa PostgreSQL automáticamente vía `docker-compose.yml`.
Kubernetes: `kubectl apply -f k8s/deployment.yaml`

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

### 1. Desplegar en Hugging Face Spaces (gratis, sin tarjeta)

[Hugging Face Spaces](https://huggingface.co/spaces) ofrece hosting Docker **sin pedir tarjeta de crédito** en el plan gratuito (CPU Basic).

1. Crea cuenta en [huggingface.co/join](https://huggingface.co/join) (gratis, sin tarjeta).
2. Ve a [huggingface.co/spaces/new](https://huggingface.co/spaces/new):
   - **Space name:** `nexbuy-ceo` (o el que prefieras)
   - **License:** MIT
   - **SDK:** Docker
   - **Visibility:** Public
3. En el Space recién creado → **Settings** → **Repository** → **Link to GitHub** → selecciona `saorjuela14-lab/ACCIONESBUSQUEDA` y rama `main`.
4. En **Settings → Variables and secrets** → **Secrets**, añade:

   | Secret | Valor |
   |--------|--------|
   | `DASHBOARD_ACCESS_TOKEN` | `Portafolio111` |

5. Opcional (cuando tengas keys): `FRED_API_KEY`, `POLYGON_API_KEY`, `ALPHA_VANTAGE_API_KEY`.
6. El Space construye el `Dockerfile` automáticamente. La URL será algo como:

   `https://huggingface.co/spaces/TU_USUARIO/nexbuy-ceo`

   La app responde en la URL pública del Space (botón **"Open app"**).

> **Nota:** En el plan gratuito el Space puede dormir tras inactividad (~1 min al despertar). La base SQLite es efímera: se reinicia en cada rebuild del Space.

### 2. Acceder al panel

| URL | Uso |
|-----|-----|
| `https://TU-USUARIO-nexbuy-ceo.hf.space/login` | Pantalla de acceso |
| `https://TU-USUARIO-nexbuy-ceo.hf.space/dashboard` | Panel CEO (PWA, móvil y escritorio) |

(La URL exacta aparece al pulsar **Open app** en tu Space.)

- Token de acceso: **`Portafolio111`**
- En el celular: abre `/login`, entra con el token y usa **Añadir a pantalla de inicio** (PWA).

### 3. Cómo pedir cambios sin PC local

1. Escribe aquí en Cursor lo que quieres (watchlist, UI, nuevas fases).
2. El agente hace commit → push a `main` en GitHub.
3. Hugging Face reconstruye el Space automáticamente (si está vinculado al repo).

### Alternativa: Koyeb (también sin tarjeta en plan Starter)

Si prefieres otra opción: [koyeb.com](https://www.koyeb.com) — plan gratuito, conecta el mismo repo GitHub, tipo **Web Service** con `Dockerfile`, y añade `DASHBOARD_ACCESS_TOKEN=Portafolio111` en variables de entorno.

## Disclaimer

For research and informational purposes only. Not financial advice.
