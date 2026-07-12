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
- **SQLite** en producción (SnapDeploy). Sin PostgreSQL ni Redis en el despliegue online.

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

### SnapDeploy (gratis, sin tarjeta)

En SnapDeploy solo verás **GitHub**, **AI Templates** o **Upload Artifact**. Usa **GitHub**.

1. [snapdeploy.dev](https://snapdeploy.dev) → **Sign up with GitHub**
2. **Deploy Your Application** → pestaña **GitHub**
3. Repo: `saorjuela14-lab/ACCIONESBUSQUEDA`, rama **`main`**
4. **Espera 1–2 min** a que GitHub tenga el último push (commit con `requirements.txt` sin PostgreSQL)
5. Si ya habías conectado el repo antes: **desconéctalo y vuelve a conectar** para refrescar el escaneo
6. Configura:
   - **Port:** `8000`
   - **Start command** (si lo pide): `uvicorn main:app --host 0.0.0.0 --port 8000`
   - **Health check:** `/health`
7. Variables de entorno — **no crees add-ons de PostgreSQL ni Redis**:

   | Variable | Valor |
   |----------|--------|
   | `DASHBOARD_ACCESS_TOKEN` | `Portafolio111` |
   | `DATABASE_URL` | `sqlite+aiosqlite:///./data/nexbuy.db` |
   | `REDIS_ENABLED` | `false` |
   | `APP_ENV` | `production` |
   | `SCHEDULER_ENABLED` | `true` |

8. Pulsa **Deploy**

> **Si aún pide PostgreSQL/Redis:** el escaneo está cacheado. Prueba **Upload Artifact** → descarga el ZIP desde [github.com/saorjuela14-lab/ACCIONESBUSQUEDA/archive/refs/heads/main.zip](https://github.com/saorjuela14-lab/ACCIONESBUSQUEDA/archive/refs/heads/main.zip) y súbelo.

La app usa **SQLite + caché en memoria**. No necesitas bases de datos externas ni add-ons de pago.

### Hugging Face (solo si tienes plan PRO)

Tu cuenta `sergio14orjuela` está activa, pero **los Spaces Docker/Gradio en plan gratuito ahora requieren suscripción PRO** en Hugging Face (error 402). Sin PRO no se puede crear el Space desde la API.

Si más adelante contratas PRO:

1. Vincular GitHub: [huggingface.co/settings/connected-applications](https://huggingface.co/settings/connected-applications) → **Connect GitHub**.
2. Crear Space → SDK **Docker** → enlazar repo `ACCIONESBUSQUEDA`.
3. Secret: `DASHBOARD_ACCESS_TOKEN` = `Portafolio111`.

El `README.md` ya incluye el bloque YAML (`sdk: docker`, `app_port: 8000`) que Hugging Face necesita.

> **Seguridad:** No compartas tu token `hf_...` en chats. Si lo hiciste, revócalo en [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) y crea uno nuevo.

### Acceder al panel

| URL | Uso |
|-----|-----|
| `https://TU-APP.snapdeploy.dev/login` | Pantalla de acceso |
| `https://TU-APP.snapdeploy.dev/dashboard` | Panel CEO (PWA, móvil y escritorio) |

- Token de acceso: **`Portafolio111`**
- En el celular: abre `/login`, entra con el token y usa **Añadir a pantalla de inicio** (PWA).

### Cómo pedir cambios sin PC local

1. Escribe aquí en Cursor lo que quieres (watchlist, UI, nuevas fases).
2. El agente hace commit → push a `main` en GitHub.
3. SnapDeploy (o GHCR + redeploy) actualiza el panel en unos minutos.

## Disclaimer

For research and informational purposes only. Not financial advice.
