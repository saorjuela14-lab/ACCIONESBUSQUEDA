# Monarch Capital — Pitch Shark Tank (proceso completo)

**Producto web:** solo terminal (`/` → `/dashboard`). Este documento y Canva **no** son la entrada de la web.  

**Deck Canva — proceso completo (10 slides, actualizado):**  
- [Ver](https://www.canva.com/d/n0xtETeYp_z4J-C) · [Editar](https://www.canva.com/d/XiqOKfFYHtlElP5)  

**Deck Canva — corto (5 slides):**  
- [Ver](https://www.canva.com/d/eCkvJnAEP1PocNn) · [Editar](https://www.canva.com/d/CD7zTedSFolPzNo)  

Incluye: descubrimiento → comité (10 agentes) → gate de consenso → Risk Desk / 2R → Autopilot LIVE → tracción → ask.  
**Terminal:** https://accionesbusqueda.fastapicloud.dev  
**Tiempo oral sugerido:** 3:00–4:00 + Q&A  
**Datos LIVE:** ago 2026 · Alpaca

---

## Qué es Monarch Capital (definición)

Un **escritorio de inversión autónomo** (CIO en código): descubre candidatos, los pasa por un **comité multi-agente**, dimensiona con riesgo 2R, ejecuta en **Alpaca LIVE** con brackets, y gestiona el libro con trail, cooldown, EOD inteligente y kill switch — visible en el Terminal CEO, con voz en español y briefings WhatsApp.

No es un chatbot que opina. **Es un desk que opera.**

---

## El corazón del pitch: cómo se escoge una acción

Cuenta esto como historia. Es lo que te diferencia.

```
Descubrimiento → Filtro de capital → Comité (agentes) → Director + estrategias
→ Gate de consenso → Risk Desk + macro → Sizing 2R → Ejecución LIVE
→ Lifecycle (stop / trail / time-stop) → Reconcile + audit
```

### 1) Descubrimiento (de dónde salen los tickers)

No partimos de “me gusta NVDA”. El sistema **caza candidatos** según el capital real:

| Fuente | Rol |
|--------|-----|
| **StockTwits** | Trending / menciones retail |
| **X (Twitter)** | Búsqueda pública `site:x.com` |
| **Reddit** | Búsqueda pública `site:reddit.com` |
| **News** | Temas → headlines → tickers |
| **Seeds micro** | Universo líquido de pennies cuando el equity es ultra-micro |
| **Watchlist** | Monitoreo cada ~30 min en horario de mercado |

**Capital-aware:** con ~$22 no busca acciones de $200. Política de precio máximo según equity (micro / small / medium). Temas “penny” cuando el libro es chico. Bonus de asequibilidad en el ranking.

**Frase oral:**  
> “Primero el mercado habla — redes, news, seeds líquidos. Luego filtramos por lo que **nuestro capital puede comprar de verdad**.”

### 2) Existen comités — no es un solo modelo

Hay un **comité de inversión multi-agente**. Cada agente entrega evidencia estructurada; **no decide solo**. Quien consolida es el **Investment Director**, con un laboratorio de estrategias (StrategyLab).

#### Agentes que **votan** (comité estricto — 10)

| Agente | Qué mira |
|--------|----------|
| `fundamental_agent` | Negocio / fundamentals |
| `technical_agent` | Precio, multi-timeframe, gaps, volumen, estructura |
| `valuation_agent` | Valoración |
| `macro_agent` | Régimen macro (Fed, CPI, curva, VIX vía FRED cuando hay key) |
| `news_agent` | Noticias / catalizadores |
| `sentiment_agent` | Sentimiento social |
| `country_risk_agent` | Riesgo país |
| `company_risk_agent` | Riesgo de empresa |
| `corporate_actions_agent` | Splits, dividendos, eventos |
| `market_dependency_agent` | Dependencias de mercado / sector |

#### Agentes de **libro** (contexto, no voto)

| Agente | Rol |
|--------|-----|
| `portfolio_agent` | Encaje con el portafolio |
| `watchlist_agent` | Seguimiento |
| `alert_agent` | Alertas |
| `investment_memory` | Memoria de tesis pasadas + recalibración de pesos |

#### StrategyLab (horizontes)

El Director no mira un solo estilo. Evalúa estrategias y exige coherencia **corto + largo**:

- **Corto:** momentum · swing · breakout  
- **Largo:** value · growth  
- **Opcional:** dividend (en pennies no debe ser SELL)

### 3) Cómo se llega al “sí” (consenso)

**Modo estricto (libros normales):**  
consenso **unánime** de votantes + BUY en horizonte corto + BUY en largo + tesis del Director en BUY → tag `committee_unanimous_dual`.

**Modo micro (capital chico, ~≤$100):**  
mayoría (≥4 BUY, ≤2 SELL, sin STRONG_SELL) + soft dual-horizon → tag `committee_majority_dual_soft`.

**Ultra-micro (≤~$30):**  
puede usar mesa técnica rápida (`micro_technical_desk`) para no morir por timeouts del host, **sin saltarse el gate de compra**: sin tag de comité / desk, **no hay auto-compra**.

**Frase oral:**  
> “Existe un comité. Diez agentes votan. El Director arma la tesis.  
> Si no hay consenso — unánime en libros grandes, mayoría dual en micro — **no compramos**. Punto.”

### 4) Risk Desk (después del comité)

Aunque el comité diga BUY, el **Risk Desk** puede frenar:

- Max posición ~30–35% equity · max sector 40% · gross ~90% · cash reserve  
- Pérdida diaria máx. ~5% → bloquea compras  
- Max posiciones abiertas · min R:R  
- Régimen macro: en risk-off reduce size; en crisis puede **bloquear buys**  
- VaR / beta de portafolio  
- Tope de orden auto: **USD 25**  
- Mercado abierto · ventana EOD sin buys nuevos  
- **Cooldown 90 min** tras un stop  
- **Kill switch** del CEO → aplana el libro

### 5) Sizing 2R (disciplina, no vibes)

| Regla | Valor |
|-------|------:|
| Stop | **8%** |
| Take profit | **16%** (= **2R**) |
| Trail | se arma tras **+5%**; micro trail ~10% desde pico |
| Time-stop | ~7–10 días (bajo agua) |
| Notional máx. auto | **$25** |

Órdenes con **brackets** Alpaca (entrada + stop + TP), GTC cuando el EOD permite carry overnight de winners.

### 6) Ejecución LIVE y ciclo del Autopilot (~cada 10 min)

Orden del loop autónomo:

1. ¿Kill switch? → abort  
2. Reconcile Alpaca ↔ libro interno  
3. Smart EOD / flatten si toca  
4. Risk status  
5. Review de holdings (reformular tesis / subir TP)  
6. Lifecycle scan (stop / trail / time-stop)  
7. Generar picks del día (discovery → comité → risk)  
8. Auto-execute solo picks con tag de comité  
9. Briefing WhatsApp (catch-up si el host dormía)

**Firma autónoma ON** por defecto: opera sin click humano, con gates duros.

### 7) Lo que ve el CEO (producto completo)

- **Terminal** Monarch: equity, índices, estado en 3 líneas, picks, portafolio, news, técnico + gaps, allocation  
- **Voz en español:** “analiza AAPL”, “precio NVDA”, “compra 1 SOUN”  
- **WhatsApp 3×/día** (apertura / almuerzo / cierre ET)  
- **Telegram / webhook** para alertas  
- **Audit trail** + reconcile continuo  
- PWA móvil  

La web **es el terminal**. La presentación vive en Canva / este MD — no en la URL pública.

---

## Guion oral (≈3:30) — habla desde el proceso

### 0:00 — Persona + misión

> “Hoy la IA escribe ensayos sobre acciones.  
> **Monarch Capital las opera** — con comité, riesgo y disciplina de firma.  
> Construí el CIO autónomo que el retail no puede contratar.”

### 0:20 — Claridad

> “No soy otra app de gráficos.  
> Soy un **escritorio de inversión autónomo** que descubre, decide en comité, dimensiona y ejecuta en Alpaca LIVE.”

### 0:40 — Problema

> “Retail y founders tienen apps, no desks.  
> El broker ejecuta. El robo va lento. El chatbot opina sin cargar riesgo.  
> El edge se pierde en sizing malo, no cortar y revenge trading.  
> **No falta información. Falta un CIO que no duerma ni se enamore del ticker.**”

### 1:05 — Solución: el proceso (aquí te detienes y detallas)

> “Les cuento **cómo escogemos una acción** — porque ahí está el producto.  
>  
> **Uno — descubrimiento.** StockTwits, X, Reddit, news y seeds líquidos.  
> Filtramos por precio que nuestro capital puede pagar. Con 22 dólares no fantaseamos con acciones de 200.  
>  
> **Dos — el comité.** Existen agentes especializados: fundamental, técnico, valoración, macro, news, sentimiento, riesgo país, riesgo empresa, corporate actions y dependencia de mercado.  
> En paralelo entregan evidencia. El **Investment Director** arma la tesis y el StrategyLab mira momentum, swing, breakout, value y growth — corto y largo.  
>  
> **Tres — el gate.** En libros grandes: consenso unánime. En micro: mayoría dual. Sin tag de comité, **cero compra automática**.  
>  
> **Cuatro — Risk Desk.** Concentración, pérdida diaria, macro, tope de 25 dólares por orden, cooldown de 90 minutos tras un stop, kill switch.  
>  
> **Cinco — 2R.** Stop 8%, take profit 16%. Brackets en LIVE. Trail tras +5%. EOD inteligente.  
>  
> **Seis — Autopilot** cada ~10 minutos: reconcile, lifecycle, picks, execute.  
> Y el CEO ve todo en el terminal, por voz o por WhatsApp tres veces al día.”

*(Aquí abre el terminal 15–20s — demo, no slides.)*

### 2:20 — Tracción LIVE (tus números, con honestidad)

| Métrica | Valor |
|--------|------:|
| Capital inicial | **USD 21.74** |
| Equity | **≈ USD 22.07** (~**+1.5%**) |
| vs ayer | **≈ −2.2%** |
| Cash / invertido | ~68% / ~32% |
| Posición | SOUN ×1 · TP 8.01 |
| Modo | LIVE · autonomía ON · gates ON |

> “Con ~22 dólares no vendo milagros.  
> Vendo un desk que **ya corre el proceso completo en dinero real** — con días verdes y rojos a la vista.”

### 2:50 — Mercado + modelo (corto)

> “El gap es desk-as-a-service: retail serio, founders, advisors.  
> Hoy: software + autonomía. Mañana: seat / suscripción. Escala: % AUM y white-label.”

### 3:05 — Competencia

| Actor | Qué hace |
|-------|----------|
| Brokers | Ejecutan |
| Robo-advisors | Asignan lento |
| Copilots / chat | Opinan |
| **Monarch** | **Comité + riesgo + ejecución + lifecycle** |

> “Nuestro moat es el loop cerrado y las políticas aprendidas en LIVE — no un prompt bonito.”

### 3:20 — Riesgos (dílos tú)

> “Volatilidad micro. Host/tech. Regulación si hay AUM de terceros.  
> Mitigación ya en código: comité, 2R, cooldown, EOD, kill switch, tope de orden.”

### 3:30 — Ask + cierre

> “Busco **[monto]** por **[X]%** — y distribución — para infra always-on, datos, compliance y multi-cuenta.  
> No prometemos alfa mágico. Prometemos **proceso**. LIVE hoy. ¿Preguntas?”

---

## Deck sugerido (12 slides) — proceso primero

1. Misión / one-liner  
2. Problema (apps ≠ desks)  
3. **Cómo escogemos una acción** (diagrama del loop)  
4. **El comité** (10 votantes + Director + StrategyLab)  
5. Gates de consenso (estricto vs micro)  
6. Risk Desk + 2R  
7. Autopilot + ejecución LIVE  
8. Demo terminal (screenshot)  
9. Tracción / números LIVE  
10. Mercado + modelo  
11. Competencia + riesgos  
12. Ask + cierre  

---

## Mapa del proyecto (para Q&A — memoriza)

**Capas:** agents → services → providers (broker/market/discovery) → FastAPI → Terminal  
**Orquestación:** AnalysisService + AutopilotService + APScheduler (asyncio; no es “solo un LLM”)  
**Gates de compra:** kill switch · tag comité · risk desk · mercado abierto · EOD · cooldown · cash/concentración · brackets  
**Ops:** `/ops/status`, autopilot, kill-switch, reconcile, lifecycle, audit  
**Brand:** Monarch Capital · ejecución Alpaca LIVE  

---

## Checklist día del pitch

- [ ] Contar el proceso de escogencia de memoria (descubrimiento → comité → risk → 2R → LIVE)  
- [ ] Nombrar que **existen comités** y ~qué miran (no hace falta listar los 10 de corrido)  
- [ ] Dec = **terminal**, 15–20s  
- [ ] Números LIVE memorizados (con el −2.2% del día)  
- [ ] Ask en una oración  
- [ ] Energía / de pie / sin “resting pitch face”  

---

## Estructura Shark Tank (investigación aplicada)

| Fuente | Aplicado aquí |
|--------|----------------|
| Mindy Zemrak | Empieza por persona+misión; claridad; energía; invierten en ti |
| O’Leary | Oportunidad en ≤90s; por qué tú ejecutas |
| Early-stage | Problema → solución **con demo del proceso** → tracción → ask |
| Este pitch | El “wow” no es la UI: es **el comité + el loop LIVE** |

---

## Links

- Terminal: `/` → `/dashboard`  
- Guion/proceso: este archivo  
- Canva 10 slides: [Ver](https://www.canva.com/d/n0xtETeYp_z4J-C) · [Editar](https://www.canva.com/d/XiqOKfFYHtlElP5)  
- Canva 5 slides: [Ver](https://www.canva.com/d/eCkvJnAEP1PocNn) · [Editar](https://www.canva.com/d/CD7zTedSFolPzNo)
