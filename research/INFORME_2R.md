# Informe 2R revisado — PR #128

Fecha de revision: 2026-10-01  
Rama: `cursor/backtest-salidas-2r-898a`  
Alcance: solo `research/`. No se tocaron servicios LIVE, flags, deploy ni ordenes.

## 0. Resumen ejecutivo

Esta version incorpora la revision de Riesgo:

- La linea base ya no es "dejar correr a 2R" sino la regla real observada/codificada de EOD: cerrar cualquier verde al final del dia (`asegurar_ganancia`) y cargar rojas, salvo stop/TP/trailing.
- Se corrigen gaps: si abre por debajo del stop, se sale al open; si abre por encima del TP, se sale al open.
- Se agregan tarifas fijas estimadas de Alpaca: CAT minimo $0.01 por dia con fills y SEC+FINRA TAF minimo $0.02 por dia con ventas, ademas de spread/slippage.
- Se abandona fraccional en el backtest base: Alpaca fractional trading es DAY-only y no soporta bracket/OCO, por lo que no deja stop GTC overnight. La simulacion usa acciones enteras.
- Se usa la regla de sizing de Riesgo: linea maxima = `min(25% * min(equity, base), 0.5 * colchon / distancia_stop)`, con colchon = `equity - 95% * base`, base $21.74. No abre si qty entera < 1 o notional < $3.
- El freno acumulado en $20.653 (= 95% de base $21.74) queda etiquetado como **freno propuesto de investigacion**, no vigente. El LIVE vigente tiene freno de perdida de sesion que bloquea compras, no un freno acumulado de cuenta.

Conclusion: **ninguna variante cumple criterios para pasar a LIVE**. Con sizing entero de Riesgo, el OOS queda con muy pocas operaciones y expectancy neta negativa en todas las variantes. La mejor lectura no es "elegir otra salida" sino: no hay evidencia suficiente de edge, el sizing de Riesgo reduce drasticamente la operabilidad con el colchon actual, y antes de LIVE hay que validar en PAPER con fills/journal correctos.

## 1. Reglas actuales y fuentes de codigo

### Entradas y universo

- La mesa micro usa seeds penny/liquidos en `services/micro_portfolio_manager_service.py:28-38`.
- Para capital <= $30, `DailyTradeRecommendationService.generate` usa fast path micro (`services/daily_trade_recommendation_service.py:92-108`).
- El scoring diario usa historico 3 meses / 1D (`services/daily_trade_recommendation_service.py:490-509`).
- La accion/horizonte premia momentum de 1D/5D y volumen (`services/daily_trade_recommendation_service.py:630-641`).
- La mesa tecnica micro aprueba por RSI/momentum/volumen cuando el comite completo no cabe en timeout (`services/micro_portfolio_manager_service.py:330-369`).

### Stop, TP y lifecycle

- Micro manager propone `stop = price * 0.92` y `target = price * 1.16` (`services/micro_portfolio_manager_service.py:241-244`).
- AutoExecute rellena stop -8% y TP +16% si faltan (`services/auto_execute_service.py:232-241`).
- El sizing live usa acciones enteras (`int(...)`) y riesgo contra stop (`services/auto_execute_service.py:244-260`).
- Alpaca bracket se construye cuando compra market con stop+TP (`services/alpaca_order_service.py:581-606`, `services/alpaca_order_service.py:790-797`).
- Lifecycle arma trailing tras +5% y usa el max(stop original, trail) (`services/position_lifecycle_service.py:229-242`).
- TP se evalua antes de stops de calendario (`services/position_lifecycle_service.py:252-259`).
- Smart EOD/carry esta en `services/intraday_flat_service.py:83-119`. La revision de Riesgo pide modelar como baseline lo observado en fills: verdes se cosechan a mercado al EOD y rojas se cargan.

### Journal no es verdad final

El journal guarda stop/TP de apertura (`database/models.py:320-329`) y el R multiple se calcula contra ese stop registrado (`database/repositories/trade_journal_repository.py:110-130`). El stop efectivo pudo diferir por trailing, reemplazos de stop GTC, EOD/carry o fills reales de broker. Para los 21 cierres reales, el informe no commitea datos de cuenta; usa solo agregados provistos por Riesgo.

## 2. Datos reales agregados provistos por Riesgo

No hay credenciales Alpaca ni `DATABASE_URL` en este entorno. Uso solo agregados de la revision:

- 21 trades reales reconstruidos desde fills.
- Septiembre: 7 trades, P&L -$1.4774, 3 stops reales.
- Fills reales muestran ganancias pequenas (+0.2% a +4%, mediana aprox. +0.8%) y perdidas cercanas a -8% en stops.
- SNAP 23 sep fue stop -8.06% (-$0.48), no ganancia.
- Tarifas estimadas desde tabla publica explican gran parte de la fuga de caja: aprox. $0.62 acumulado.

## 3. Backtest revisado

Codigo: `research/backtest/run_2r_backtest.py`.

### Metodologia

- Datos publicos: yfinance 1D ajustado por splits.
- Senales sin look-ahead: barra cerrada -> entrada siguiente open.
- Ejecucion: acciones enteras, no fraccionales.
- Base: $21.74; capital inicial de simulacion: $21.76.
- Freno acumulado propuesto: 95% de base = $20.653. No es el freno LIVE vigente.
- Sizing Riesgo: `min(25% * min(equity, base), 0.5 * colchon / 8%)`; no trade si qty < 1 o notional < $3.
- Gaps: stop/TP al open si el open cruza el nivel.
- Trailing conservador: no arma trailing con el high del mismo dia antes de evaluar el low; usa peak previo.
- Costos: spread/slippage por lado (75/35/25/15 bps segun precio) + tarifas fijas estimadas.
- Split: IS antes de 2025-12-03; OOS desde 2025-12-03. Cada tramo reinicia la cuenta en $21.76 para comparar.

Tickers sin barras suficientes: BITF, MPW, NKLA, TWO, WISH. Esto no elimina el sesgo de supervivencia de yfinance; solo lo hace explicito.

### Variantes

| Variante | Descripcion |
|---|---|
| `actual_eod` | Baseline real: cosecha cualquier verde al EOD; rojas cargan salvo stop/TP/trailing/time-stop. |
| `simetrica` | Sin cosecha de verdes; stop/TP/trailing; carga overnight solo si P&L >= -0.5R; si no, `eod_risk_cut`. |
| `simetrica_trend` | `simetrica` + filtro ADX > 25 o precio sobre SMA50. |
| `cooldown_1d` | `simetrica_trend` + no reentrar mismo simbolo 1 sesion tras stop; si stop >= 1R, 1 sesion sin nuevas entradas globales. |
| `cooldown_5d` | Igual, pero 5 sesiones sin reentrar mismo simbolo tras stop. |

Hipotesis no backtesteada: alineacion noticias/sentimiento. No hay historico de agentes para backtest honesto; queda solo como hipotesis PAPER.

## 4. Resultados in-sample y out-of-sample

Metricas netas de tarifas.

| Tramo | Variante | Trades | Win % | Exp. R neta | PF neto | DD cuenta | Equity final | Freno prop. | Freno sesion | Stop gaps | Fees |
|---|---|---:|---:|---:|---:|---:|---:|---|---|---:|---:|
| IS | actual_eod | 36 | 52.78 | -0.0435 | 0.8339 | -8.0751% | $21.0463 | no | no | 2 | $1.23 |
| OOS | actual_eod | 6 | 33.33 | -0.5080 | 0.1843 | -5.1397% | $20.8390 | no | no | 0 | $0.23 |
| IS | simetrica | 19 | 31.58 | -0.1246 | 0.7752 | -8.6383% | $20.9602 | no | no | 0 | $0.71 |
| OOS | simetrica | 2 | 0.00 | -1.0016 | 0.0000 | -3.1866% | $21.0666 | no | no | 1 | $0.08 |
| IS | simetrica_trend | 14 | 28.57 | -0.1906 | 0.6772 | -6.2729% | $21.0633 | no | no | 0 | $0.53 |
| OOS | simetrica_trend | 2 | 0.00 | -1.0016 | 0.0000 | -3.1866% | $21.0666 | no | no | 1 | $0.08 |
| IS | cooldown_1d | 14 | 28.57 | -0.1906 | 0.6772 | -6.2729% | $21.0633 | no | no | 0 | $0.53 |
| OOS | cooldown_1d | 2 | 0.00 | -1.0016 | 0.0000 | -3.1866% | $21.0666 | no | no | 1 | $0.08 |
| IS | cooldown_5d | 14 | 28.57 | -0.1906 | 0.6772 | -6.2729% | $21.0633 | no | no | 0 | $0.53 |
| OOS | cooldown_5d | 2 | 0.00 | -1.0016 | 0.0000 | -3.1866% | $21.0666 | no | no | 1 | $0.08 |

Notas:

- Con sizing entero de Riesgo, ninguna variante toca el freno acumulado propuesto en IS/OOS, pero esto ocurre porque el sizing reduce mucho la exposicion y filtra muchas entradas.
- El freno LIVE de sesion aproximado tampoco se dispara en esta simulacion; se modela como perdida realizada de una sesion/trade <= -5% de equity. El codigo LIVE real bloquea compras por perdida de sesion y no necesariamente cierra posiciones.
- `actual_eod` tiene mas trades porque cierra verdes rapidamente y libera capital; OOS sigue negativo.
- Las variantes simetricas OOS solo tienen 2 trades bajo esta regla de sizing, por lo que su estadistica no es suficiente.

### Distribucion de salidas

| Tramo | Variante | Salidas |
|---|---|---|
| IS | actual_eod | asegurar_ganancia 24, stop 7, stop_trailing 2, stop_gap 1, stop_trailing_gap 1, take_profit 1 |
| OOS | actual_eod | asegurar_ganancia 3, stop 3 |
| IS | simetrica | stop_trailing 6, eod_risk_cut 5, take_profit 4, stop 4 |
| OOS | simetrica | stop_trailing_gap 1, time_stop 1 |
| IS | simetrica_trend / cooldowns | stop_trailing 5, eod_risk_cut 4, take_profit 2, stop 2, time_stop 1 |
| OOS | simetrica_trend / cooldowns | stop_trailing_gap 1, time_stop 1 |

## 5. Estadistica e IC

| Tramo | Variante | n | Exp. R neta | IC95 R | Cota inferior IC90 unilateral | n para distinguir +0.2R de 0 |
|---|---|---:|---:|---|---:|---:|
| IS | actual_eod | 36 | -0.0435 | [-0.2948, 0.2077] | -0.2078 | 25 |
| OOS | actual_eod | 6 | -0.5080 | [-1.1266, 0.1107] | -0.9125 | 25 |
| IS | simetrica | 19 | -0.1246 | [-0.6277, 0.3784] | -0.4536 | 52 |
| OOS | simetrica | 2 | -1.0016 | [-1.0472, -0.9561] | -1.0314 | 1 |
| IS | simetrica_trend | 14 | -0.1906 | [-0.6942, 0.3129] | -0.5199 | 38 |
| OOS | simetrica_trend | 2 | -1.0016 | [-1.0472, -0.9561] | -1.0314 | 1 |
| IS | cooldown_1d | 14 | -0.1906 | [-0.6942, 0.3129] | -0.5199 | 38 |
| OOS | cooldown_1d | 2 | -1.0016 | [-1.0472, -0.9561] | -1.0314 | 1 |
| IS | cooldown_5d | 14 | -0.1906 | [-0.6942, 0.3129] | -0.5199 | 38 |
| OOS | cooldown_5d | 2 | -1.0016 | [-1.0472, -0.9561] | -1.0314 | 1 |

Advertencia: con n=2, el IC es mecanico y no debe usarse para afirmar edge. Sirve solo para mostrar que el sizing entero deja poca muestra OOS. Para afirmar una expectativa de +0.2R se necesitan decenas de cierres incluso con desviacion estandar moderada; Riesgo propone minimo 40 cierres para revisar integridad/riesgo y >=100 cierres o 60 sesiones para afirmar edge.

## 6. Evaluacion contra gates de Riesgo

Gates oficiales de la revision (G1-G8):

| Gate | Umbral | Resultado backtest |
|---|---|---|
| G1 Muestra | >=40 cierres y >=20 sesiones para revision; >=100 cierres o 60 sesiones para afirmar edge | Ninguna variante OOS cumple. IS solo `actual_eod` se acerca con 36, pero no llega a 40. |
| G2 Expectancy neta | >= +0.10R y cota inferior IC90 > 0 | Ninguna cumple; todas las expectancies netas son negativas. |
| G3 Profit factor neto | >= 1.15 | Ninguna cumple. Mejor IS `actual_eod` PF 0.8339; OOS `actual_eod` PF 0.1843. |
| G4 DD max cuenta | <= 50% del colchon inicial (~$0.54, aprox. 2.5%) | Ninguna cumple de forma robusta; OOS simetricas tienen DD -3.19%, actual_eod -5.14%. |
| G5 Peor racha | <=3 perdidas seguidas y <=3% equity | Varias muestras pequenas parecen cumplir por bajo n; no compensa fallos G1-G4. |
| G6 Freno acumulado propuesto | Cero disparos | Cumplen en backtest revisado, por sizing pequeno; no significa edge. |
| G7 Integridad journal-fills | 100% cierres con exit_price = fill, 0 fantasmas, diferencia P&L <= $0.05 | No evaluable con yfinance. Revision real indica que hoy NO se cumple en fills/journal reales. |
| G8 Reconciliacion caja | Equity = base + realizado + no realizado - fees con residuo <= $0.05/semana | No evaluable con yfinance. Riesgo encontro fuga explicable por fees y residuo pequeno, pero requiere activities de Alpaca. |

Controles adicionales G9-G13 de la revision:

- G9 proteccion broker: no evaluable aqui; debe verificarse con ordenes abiertas Alpaca.
- G10 enfriamiento post-stop: se modelo en variantes cooldown, pero la muestra OOS es insuficiente.
- G11 entry mandate = fill: no evaluable en backtest; Riesgo detecto bug operativo fuera de `research/`.
- G12 calendario earnings: no modelado; falta fuente historica.
- G13 diversidad de salidas: ninguna variante tiene evidencia suficiente; `actual_eod` IS esta dominada por `asegurar_ganancia`.

**Dictamen:** ninguna variante pasa a LIVE. La candidata recomendada para PAPER no es una ganadora estadistica; es la familia `simetrica_trend + cooldown`, solo porque incorpora controles de riesgo mas sanos. Debe probarse forward con gates G1-G13.

## 7. Cambios vs version anterior del PR

| Tema | Antes | Ahora |
|---|---|---|
| Baseline | 2R hold teorico | `actual_eod`: cosecha verdes EOD y carga rojas, como pidio Riesgo |
| Sizing | Fraccional, 35% equity, $5 minimo | Acciones enteras, regla de colchon de Riesgo, $3 minimo |
| Freno | Kill-switch acumulado tratado como regla | Etiquetado como freno acumulado propuesto; se reporta freno de sesion aproximado |
| Gaps | Stop/TP al nivel | Gap a open si open cruza stop/TP |
| Trailing | Podia armar con high del mismo dia | Usa peak previo para evitar optimismo intrabarra |
| Costos | Spread/slippage | Spread/slippage + fees fijos estimados Alpaca |
| Estadistica | Sin IC | IC95, cota IC90 unilateral y n requerido para +0.2R |
| Variantes | TP 1.5R, BE, ADX, stagnation | actual_eod, simetrica, simetrica_trend, cooldown 1/5 sesiones |
| Gates | Criterios internos parciales | Gates G1-G8 evaluados y G9-G13 comentados |

## 8. Recomendacion

1. No cambiar LIVE con base en este backtest.
2. No usar fraccionales para esta mesa mientras se requiera stop GTC/bracket overnight.
3. Si se prueba algo en PAPER, usar `simetrica_trend + cooldown` con acciones enteras y regla de sizing de Riesgo.
4. Exigir gates G1-G13 antes de cualquier promocion.
5. Antes de paper serio, corregir fuera de `research/` los problemas operativos detectados por Riesgo: integridad journal-fills, entrada de mandato = fill real, stops GTC nocturnos, enfriamiento post-stop y reconciliacion de fees.

## 9. Archivos generados

- `research/backtest/run_2r_backtest.py`
- `research/backtest/output/trades.csv`
- `research/backtest/output/metrics.csv`
- `research/backtest/output/metadata.json`
