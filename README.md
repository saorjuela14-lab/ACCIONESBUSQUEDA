---
title: Monarch Capital
emoji: 👑
colorFrom: green
colorTo: gray
sdk: docker
app_port: 8000
pinned: false
---

# Monarch Capital

Autonomous multi-agent investment desk — research, risk discipline, and LIVE execution.

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
| **X (Twitter)** | Búsqueda gratuita `site:x.com` vía DuckDuckGo (sin API de pago) |
| **Reddit** | Búsqueda `site:reddit.com` (DDG) |
| **Seeking Alpha** | Búsqueda `site:seekingalpha.com` |
| **Yahoo Finance** | Búsqueda `site:finance.yahoo.com` |

Cuando Reddit apruebe tu app OAuth, se conecta sin rehacer el agente.

### Fase 3 — Automatización (implementado)
- **Watchlist Engine**: monitoreo automático cada 30 min (horario de mercado)
- **Alert Engine**: deduplicación 24h, sin spam
- **Push Alerts** (opcional): Telegram + webhook genérico al emitir alertas
- **Asistente de voz** (opcional): comandos en español (Web Speech STT) + respuesta con voz ElevenLabs (tipo secretaria / Friday; fallback al navegador)
- **Market Monitor**: reportes 08:30 / 11:30 / 15:00 / 17:30 ET
- **Daily Investment Report**: generado a las 17:30 ET
- **Investment Memory**: evaluación automática a los 90 días + recalibración de pesos

### Risk Desk + macro (gestión de riesgo)

El escritorio de riesgo aplica **límites duros** a compras (concentración, cash mínimo, pérdida diaria, stops) y un **régimen macro** (Fed, CPI, curva, VIX) que cambia tamaño y puede bloquear compras en crisis.

| Endpoint | Uso |
|----------|-----|
| `GET /api/v1/risk/status` | Política + macro + libro Alpaca |
| `GET /api/v1/risk/macro` | Solo régimen macro |

Firma autónoma (ON por defecto — opera sin click humano):

```
FIRM_AUTONOMY=true
AUTO_EXECUTE_TRADES=true
AUTO_EXECUTE_LIVE=true
AUTO_EXECUTE_MAX_NOTIONAL=25
AUTOPILOT_INTERVAL_MINUTES=10
```

Compras solo con **consenso unánime del comité** (BUY corto+largo) + risk desk OK + mercado abierto.  
Cierres por lifecycle (stop / trailing / time-stop). Panic: `POST /ops/kill-switch/on`.

### Escritorio autónomo tipo firma de capital

1. ~~Risk Desk + macro en picks y órdenes~~
2. ~~Ciclo de vida (trailing / time-stop / invalidar tesis → vender)~~
3. ~~Reconciliación continua Alpaca ↔ DB + audit log~~
4. ~~Auto-execute LIVE con límites + comité unánime~~
5. ~~VaR / beta / sectores duros + kill switch (pánico → flat)~~
6. ~~Comité completo: memoria como evidencia + SELL → exit~~
7. ~~Autopilot programado (reconcile → risk → lifecycle → picks → execute)~~

```
GET  /api/v1/ops/status
POST /api/v1/ops/autopilot/run
POST /api/v1/ops/autopilot/promote-live
POST /api/v1/ops/kill-switch/on  {"confirm":true,"flatten":true}
POST /api/v1/ops/reconcile
POST /api/v1/ops/lifecycle/scan
GET  /api/v1/ops/audit
GET  /api/v1/ops/risk-metrics
```

**Firma autónoma:** el comité filtra compras; el scheduler corre Autopilot ~cada 30 min en horario de mercado; lifecycle cierra; kill switch aplana el libro.

```bash
python main.py serve      # API + scheduler integrado
python main.py scheduler  # Solo scheduler (standalone)
POST /api/v1/watchlist/scan  # Scan manual
GET  /api/v1/alerts
POST /api/v1/alerts/test-push
GET  /api/v1/reports/daily/latest
GET  /api/v1/risk/status
GET  /api/v1/ops/status
```

#### Alertas push (Telegram + WhatsApp)

**Telegram (alertas):**
1. Crea un bot con [@BotFather](https://t.me/BotFather) → token
2. Obtén tu `chat_id` (p. ej. `@userinfobot`)
3. Variables: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

**WhatsApp (status apertura/cierre del portafolio):**  
Se envía **3 veces por día hábil** (hora NY): **09:35 apertura**, **12:30 almuerzo**, **16:05 cierre** — equity, posiciones, órdenes abiertas y cerradas del día. No es un mensaje cada pocos minutos. Si el host cloud dormía a la hora exacta, el arranque y `/health` recuperan los slots pendientes (máx. 1 por horario, hasta 23:59 ET). El workflow GitHub **Desk keepalive** pinea `/health` en horario de mercado para que el cron no se pierda.

Opción más simple — [CallMeBot](https://www.callmebot.com/blog/free-api-whatsapp-messages/):
1. Guarda en contactos el bot actual: **+34 644 78 33 97** (los números viejos dejan de tener WhatsApp)
2. Envíale: `I allow callmebot to send me messages`
3. El bot responde con tu `apikey` (si no llega en 2 min, reintenta tras 24h)
4. Variables en FastAPI Cloud:

| Variable | Valor |
|----------|--------|
| `WHATSAPP_PHONE` | Tu número internacional sin `+` (ej. `573001234567`) |
| `WHATSAPP_API_KEY` | Apikey de CallMeBot |
| `WHATSAPP_BRIEFING_TIMES` | `09:35,12:30,16:05` (default) |

Alternativas Business: Meta Cloud API (`WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_TO`) o Twilio (`TWILIO_*` + `WHATSAPP_TO`).

Endpoints:
```
GET  /api/v1/alerts/push-status
POST /api/v1/alerts/test-push
GET  /api/v1/alerts/briefing/preview
POST /api/v1/alerts/briefing/send?session_kind=open|lunch|close|manual
```

Redeploy. En el panel → **Probar push** (también dispara un status manual).

#### Trading con Alpaca (LIVE / dinero real)

1. Crea/verifica cuenta brokerage en [Alpaca](https://app.alpaca.markets/brokerage/dashboard/overview) → **API Keys** → Generate New Keys
2. En FastAPI Cloud → Environment Variables (márcalas como Secret):

| Variable | Valor |
|----------|--------|
| `ALPACA_API_KEY` | Key ID de cuenta **LIVE** |
| `ALPACA_SECRET_KEY` | Secret Key LIVE |
| `ALPACA_PAPER` | `false` (default) |
| `ALPACA_LIVE_TRADE` | `true` (compatible con [alpacahq/cli](https://github.com/alpacahq/cli)) |
| `ALPACA_DATA_FEED` | `iex` (gratis) o `sip` si tienes suscripción |

3. Redeploy. El panel mostrará **Alpaca LIVE · dinero real** (+ mercado abierto/cerrado).
4. **Doctor** verifica trading + market data; **Gestionar capital** → **Ejecutar en Alpaca**.

Misma key alimenta **Trading API** y **Market Data**. Cada orden lleva `client_order_id` (idempotencia, como el CLI).

API: `GET /broker/status|doctor|clock`, `POST /broker/execute/*`, `DELETE /broker/orders` (cancel-all).
Opcional en tu máquina: `brew install alpacahq/tap/cli` para `alpaca account get` / `alpaca doctor`.

#### Asistente de voz (Chrome / Edge)

Botón **🎙 Voz** en el header del panel. Ejemplos:

- «¿Cómo está el mercado?» · «Analiza VRT» · «Escanea la watchlist»
- «Recomendaciones del día» · «Agrega RKLB a watchlist» · «Descubre biotech»
- «Mi portafolio» · «Mis alertas» · «Ayuda»

API: `POST /api/v1/voice/command` con `{ "text": "..." }`.
TTS: `POST /api/v1/voice/tts` (ElevenLabs) · `GET /api/v1/voice/tts/status`.

```bash
ELEVENLABS_API_KEY=tu_key
ELEVENLABS_VOICE_ID=EXAVITQu4vr4xnSDxMaL   # Sarah — cálida / profesional
ELEVENLABS_MODEL_ID=eleven_flash_v2_5      # baja latencia en español
```

En FastAPI Cloud, marca `ELEVENLABS_API_KEY` como **Secret** y haz Redeploy.

### Fase 2.2 — Market Data (implementado)
Cadena de fallback automática: **Alpaca → Polygon → Alpha Vantage → YFinance**

```bash
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
ALPACA_DATA_FEED=iex
POLYGON_API_KEY=your_key
ALPHA_VANTAGE_API_KEY=your_key
POLYGON_PER_MINUTE_LIMIT=5
ALPHA_VANTAGE_DAILY_LIMIT=25
```

Con keys de Alpaca, cotizaciones e histórico salen de `data.alpaca.markets`. Si Alpaca falla o no está configurada, Polygon / Alpha Vantage / YFinance toman el relevo.

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

### Desplegar en FastAPI Cloud (Hobby — **se duerme sin tráfico**)

[FastAPI Cloud](https://fastapicloud.com) es la plataforma oficial del equipo FastAPI. Plan **Hobby gratis**, sin tarjeta, hasta 3 apps. Integración directa con GitHub.

> **Importante:** en Hobby el proceso **se apaga** si nadie lo visita. Por eso a veces fallan los WhatsApp de 09:35 / 12:30 / 16:05. Mitigación: workflow **Desk keepalive** (GitHub Actions cada 5 min).  
> **Alternativa always-on (recomendada para la firma):** [Railway](#railway-always-on) — el proceso no hiberna.

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
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/nexbuy.db` **o** Postgres Neon (ver abajo) |
| `REDIS_ENABLED` | `false` |
| `APP_ENV` | `production` |
| `SCHEDULER_ENABLED` | `true` |
| `TELEGRAM_BOT_TOKEN` | *(opcional)* Token del bot Telegram |
| `TELEGRAM_CHAT_ID` | *(opcional)* Chat ID para alertas push |
| `ALPACA_API_KEY` | *(recomendado)* Key brokerage LIVE |
| `ALPACA_SECRET_KEY` | *(recomendado)* Secret brokerage LIVE |
| `ALPACA_PAPER` | `false` (LIVE / dinero real) |
| `ALPACA_LIVE_TRADE` | `true` (alias CLI; gana sobre PAPER) |
| `ALPACA_DATA_FEED` | `iex` (default) |
| `ELEVENLABS_API_KEY` | *(recomendado)* Key TTS para voz amigable del asistente |
| `ELEVENLABS_VOICE_ID` | `EXAVITQu4vr4xnSDxMaL` (Sarah; opcional) |
| `ELEVENLABS_MODEL_ID` | `eleven_flash_v2_5` (opcional) |

Marca `DASHBOARD_ACCESS_TOKEN`, keys Alpaca, `ELEVENLABS_API_KEY` y `DATABASE_URL` como **Secret** si la opción existe. Pulsa **Redeploy** tras guardar.

#### Persistencia con Neon Postgres (recomendado)

SQLite en FastAPI Cloud **se borra en cada redeploy**. Para conservar portafolios, watchlist e historial:

1. Crea cuenta gratis en **[console.neon.tech](https://console.neon.tech)**
2. **Create project** → copia la connection string (tipo `postgresql://...@ep-xxx.neon.tech/neondb?sslmode=require`)
3. En FastAPI Cloud → Environment Variables:

```
DATABASE_URL=postgresql://USER:PASSWORD@ep-XXXX.neon.tech/neondb?sslmode=require
```

(La app la convierte sola a `postgresql+asyncpg://...`.)

4. Redeploy. Al arrancar se crean las tablas automáticamente.
5. En el panel: **Sincronizar desde Alpaca** una vez para cargar cash/posiciones.

> Con Postgres ya **no** pierdes datos al redesplegar. El sync Alpaca sigue siendo útil para alinear el panel con el broker.

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

### Railway always-on {#railway-always-on}

Railway mantiene el proceso **siempre encendido** (no hiberna como FastAPI Cloud Hobby). El repo ya incluye `Dockerfile` + `railway.toml`.

```bash
# En el repo
railway login          # o railway up -y (crea proyecto + deploy)
railway up -y
railway domain         # genera URL pública https://….up.railway.app
```

Copia **las mismas variables** del panel FastAPI Cloud (Alpaca, WhatsApp CallMeBot, Telegram, `DATABASE_URL` Postgres Neon, `FIRM_AUTONOMY`, etc.) a Railway → Variables. Luego:

1. En GitHub → **Settings → Variables**: `RAILWAY_KEEPALIVE_URL` = tu URL Railway `/health`
2. Opcional: apunta el dashboard a la URL Railway y deja FastAPI Cloud como respaldo
3. El workflow **Desk keepalive** pinea ambas URLs

Con Railway como primario, los 3 WhatsApp (apertura / almuerzo / cierre) salen a la hora aunque nadie abra el panel.

### Otras plataformas (referencia)

| Plataforma | Motivo |
|------------|--------|
| Railway | **Always-on** (recomendado para briefings + autopilot) |
| Zeabur | Obliga a comprar servidor |
| Render | Pide tarjeta |
| SnapDeploy | Pide add-ons PostgreSQL/Redis |

## Disclaimer

For research and informational purposes only. Not financial advice.
