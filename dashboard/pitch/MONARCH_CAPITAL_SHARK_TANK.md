# Monarch Capital — Presentación para inversionistas (ES)

**Actualizado:** 6 ago 2026 · datos LIVE Alpaca  
**Deck Canva:** [Ver](https://www.canva.com/d/Okhpj0q7BYqRTFM) · [Editar](https://www.canva.com/d/FqQgkNCyk_R8qwD)  
**Producto:** https://accionesbusqueda.fastapicloud.dev  
**Tiempo pitch:** 3–4 min + Q&A

---

## 1. Qué es el proyecto

**Monarch Capital** es un **escritorio de inversión autónomo** (CIO en software):

- Descubre oportunidades asequibles al capital real de la cuenta  
- Pasa por un **comité multi-agente** (técnico, fundamental, news, macro, risk)  
- **Dimensiona** la posición por presupuesto de riesgo en el stop  
- **Ejecuta** en Alpaca LIVE con brackets y reconcile  
- **Revisa** cada ciclo: take-profit, trail, cooldown, EOD inteligente  

No es un chatbot que “opina” sobre acciones. Es un **loop cerrado** que decide, opera y cosecha con reglas en código.

**One-liner para el inversionista:**  
> El CIO autónomo que el retail no puede contratar — con comité, riesgo 2R y ejecución LIVE.

---

## 2. Cómo vamos (capital ingresado — cifras LIVE)

| Métrica | Valor |
|--------|------:|
| Capital inicial (libro) | **USD 21.74** |
| Equity actual | **≈ USD 22.07** |
| Retorno vs inicial | **≈ +1.5%** |
| Equity cierre ayer | USD 22.56 |
| Variación vs ayer | **≈ −2.2%** |
| Cash | USD 14.97 (~68%) |
| Invertido | ~USD 7.10 (~32%) |
| Posición abierta | **SOUN ×1** · entrada 7.16 · mark ~7.10 (−0.9%) |
| Orden protectora | Take-profit bracket **8.01** (GTC) |
| Modo | **Alpaca LIVE** (no paper) |
| Autonomía de firma | **ON** · kill switch listo |
| Cuenta desde | 21 jul 2026 |

**Cómo explicarlo sin maquillaje:**

1. El capital micro **no es el producto** — es la **prueba de fuego** del proceso.  
2. Ya compramos, protegimos y cerramos en dinero real.  
3. El equity puede bajar un día (hoy vs ayer −2.2%) y eso se cuenta con transparencia.  
4. Cash ~68%: no estamos overexposed; hay pólvora para el siguiente ciclo.  
5. Cada error (stop-out, overnight, revenge) se convirtió en **política de código**.

**Frase clave:**  
> “No vendo CAGR inventado con 22 dólares. Vendo un desk que ya opera LIVE y se endurece solo.”

---

## 3. Estrategia que usamos

### 3.1 Loop operativo (cada ~10 min en horario de mercado)

1. **Reconcile** Alpaca ↔ libro interno  
2. **Cash sweep** / higiene de cuenta  
3. **EOD smart** (cerca del close): asegura verdes; carry rojo solo si merece recuperación (máx −8%)  
4. **Holdings review**: ¿cosechar, trail, o mantener tesis?  
5. **Lifecycle**: stops, targets, trail armado, time-stop solo si underwater  
6. **Discovery + picks** asequibles al equity real  
7. **Auto-execute** con tope micro y risk desk OK  

### 3.2 Disciplina de riesgo (política actual)

| Regla | Parámetro | Para qué |
|-------|-----------|----------|
| Stop micro | **8%** | Cortar perdedora sin drama |
| Take-profit | **16% (2R)** | Objetivo simétrico al riesgo |
| Trail | **10%** solo tras **+5%** | No trail prematuro |
| Cooldown post-stop | **90 min** | Anti–revenge trading |
| Máx. posición | **30%** del equity | Concentración controlada |
| Tope orden | **USD 25** | Fase micro segura |
| Risk desk | VaR 8% · beta 1.8 · sector 40% | Caps de libro |
| Kill switch | 1 clic | CEO apaga la firma |

### 3.3 Filosofía

- **Proceso > predicción:** el edge está en sizing, salida y disciplina, no en “adivinar el ticker”.  
- **LIVE > paper:** el sistema aprende con fricción real (fills, gaps, emociones).  
- **Código > slide:** si no está en el motor, no cuenta como estrategia.

---

## 4. Riesgos (ser brutales y claros)

### Riesgos de mercado / operación
- Drawdown intradía y overnight en nombres volátiles (micro-cap / AI names como SOUN)  
- Liquidez y slippage en cuentas chicas  
- Régimen bearish (hoy el panel marca sesgo bajista)  

### Riesgos de producto / tech
- Host Hobby puede hibernar → mitigamos con keepalive / always-on (Railway recomendado)  
- Dependencia de APIs (broker, datos, LLM)  
- Modelo puede alucinar tesis → **mitigado**: risk rules y sizing son código duro  

### Riesgos de negocio / regulación
- Hoy: herramienta sobre broker regulado (Alpaca), no hedge fund público  
- Para AUM de terceros: hace falta compliance pack, roles, audit trail, asesoría legal  
- Competencia de brokers, robo-advisors y copilots  

### Cómo los controlamos
Kill switch · brackets GTC · cooldown · EOD smart · max position · tope notional · reconcile continuo.

---

## 5. Oportunidades

| Oportunidad | Por qué importa |
|-------------|-----------------|
| **Gap de mercado** | Retail tiene apps, no desks. Nadie cierra el loop decisión→ejecución→cosecha con disciplina explícita. |
| **Timing** | LLMs + APIs de broker + datos permiten un CIO software-first ahora. |
| **Prueba LIVE** | Ya no es deckware: hay autonomía, órdenes y políticas en producción. |
| **B2B white-label** | Advisors, neobanks y educadores financieros necesitan “desk as a service”. |
| **Datos propios** | Cada decisión/auditoría genera dataset de proceso (moat a largo plazo). |
| **Emoción como bug** | Revenge trading y FOMO son epidemic; cooldown + 2R son producto. |

---

## 6. Crecimiento y proyecciones

> Importante: **no proyectamos retorno mágico** del capital micro. Proyectamos **capacidad de escalar el proceso**.

| Escenario | Horizonte | Qué significa | Señal de éxito |
|-----------|-----------|---------------|----------------|
| **A — Validación** | 3–6 meses | Seguir LIVE micro, endurecer políticas, uptime always-on | Drawdown controlado + loop estable |
| **B — Desk operador** | Siguiente hito | Capital **USD 5k–25k** con mismos guardrails 2R | Mismo proceso, mayor notional |
| **C — Multi-cuenta** | Escala | White-label / seats para advisors y neobanks | N desks, suscripción + % AUM |

### Modelo de negocio (evolución)
1. **Hoy:** software de terminal + autonomía (1 desk)  
2. **Mañana:** suscripción por cuenta + seat de desk  
3. **Escala:** % AUM bajo mandato + white-label B2B  

### Ask al inversionista
- Capital para: infra always-on, datos, compliance, onboarding multi-cuenta  
- Y sobre todo **distribución** (brokers, neobanks, educadores)  
*(Di tu cifra de ronda en una oración si ya la tienes.)*

---

## 7. Guion hablado (3–4 min)

### Apertura (15 s)
> “Hoy la IA escribe ensayos sobre acciones. **Monarch Capital las opera** — con comité, riesgo y disciplina de firma.”

### Problema (25 s)
Retail no tiene desk. Brokers ejecutan, robo-advisors van lento, chatbots opinan sin cargar el riesgo. El edge se pierde en sizing, stops y revenge trading.  
**Punch:** *No falta información. Falta un CIO que no duerma ni se enamore del ticker.*

### Solución (30 s)
Loop: descubre → comité → dimensiona 2R → ejecuta LIVE → revisa/cosecha. Autopilot 10 min. Tope $25/orden en fase micro.

### Cómo vamos (40 s)
Inicial **21.74** → equity **~22.07** (~+1.5%). Vs ayer ~−2.2%. SOUN ligeramente roja, TP 8.01. Cash ~68%. Autonomía ON.  
> El valor del micro es que el loop ya corrió en LIVE.

### Riesgos (25 s)
Stop 8 / TP 16 / trail tras +5 / cooldown 90m / EOD smart / kill switch. El LLM no manda solo.

### Oportunidades + proyecciones (35 s)
Gap desk-as-a-service. Escenarios A→B→C: micro → $5–25k → multi-cuenta. Norte: proceso + drawdown, no ROI teatral.

### Ask + cierre (25 s)
Capital + distribución para pasar de 1 desk a N desks.  
> No prometemos alfa mágico. Prometemos proceso. Preguntas.

---

## 8. Q&A listo

**¿Track record?** Micro LIVE + políticas documentadas. Transparencia de PnL día a día.  
**¿Cómo ganan?** Software → sub/seat → AUM / white-label.  
**¿Regulación?** Broker regulado hoy; compliance antes de AUM de terceros.  
**¿Moat?** Loop cerrado + políticas aprendidas en LIVE + datos de decisión.  
**¿Qué salió mal?** Stop sin cooldown → 90m; overnight giveback → EOD smart; sizing emocional → risk budget.

---

## 9. Demo en vivo (opcional)

1. Landing `/` — marca Monarch + KPIs LIVE  
2. Terminal `/dashboard` — risk desk / ops / autonomía  
3. Mostrar posición SOUN + kill switch  
4. Abrir deck Canva en modo presentador  
