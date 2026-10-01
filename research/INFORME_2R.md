# Informe 2R — salidas, estancamiento y validacion PAPER

Fecha de investigacion: 2026-10-01  
Rama: `cursor/backtest-salidas-2r-898a`  
Modo: investigacion/offline. No se consulto ni modifico LIVE.

## 0. Resumen ejecutivo

- No hubo acceso a `DATABASE_URL`, SQLite local ni credenciales Alpaca en el entorno de Cursor. Por tanto, no puedo listar con precision los 12 cierres reales desde el journal/broker. No invento esos trades.
- El codigo operativo actual abre brackets con stop/TP fijos sobre entrada: stop -8% y TP +16% para micro-capital. Luego el stop efectivo puede diferir del journal por trailing, BE de variantes, sync de stop GTC y Smart EOD/carry.
- Con datos publicos y una simulacion de cuenta real ($21.76, fraccionales $5-$25, max 35% por nombre, costos/spread), todas las variantes probadas disparan el kill-switch OOS de 5% ($20.67). La mejor por expectancy OOS fue `adx25_2R`, pero aun negativa: -0.050R/trade, PF 0.86, kill el 2026-09-23.
- Evidencia principal: el objetivo 2R queda lejos para el horizonte: en OOS la variante actual tuvo MFE mediana 0.46R y TP mediano 2.84 ATR; solo 1/7 llego a TP antes del kill. Bajar a 1.5R no arreglo el problema.
- Recomendacion: no pasar cambios a LIVE. En PAPER probar una variante nueva basada en `adx25_2R` + filtro anti-chase/extension + objetivo ATR-dinamico; aprobar solo si supera criterios cuantitativos abajo.

## 1. Reglas actuales documentadas en codigo

### 1.1 Universo, entradas y horizonte

- La mesa micro usa semillas liquidas/penny en `services/micro_portfolio_manager_service.py:28-38`. El comentario excluye shells/deslistadas como NKLA/WISH/BBIG del seed vivo.
- Para capital <= $30, `DailyTradeRecommendationService.generate` toma el fast path micro y delega en `MicroPortfolioManagerService.manage` (`services/daily_trade_recommendation_service.py:92-108`).
- El tecnico multi-timeframe analiza 5m, 15m, 30m, 1H, 4H, 1D, 1W y 1M (`agents/technical_agent.py:29-37`), pero el scoring de candidatos diarios usa historico 3 meses / 1D (`services/daily_trade_recommendation_service.py:490-509`).
- El clasificador de corto plazo compra si 1D > +2% con volumen >= 1.5x, o swing si 5D > +5%, con horizontes "1-3 dias", "1-2 semanas" o "3-7 dias" (`services/daily_trade_recommendation_service.py:630-641`).
- La mesa tecnica micro aprueba por RSI/momentum/volumen cuando el comite completo no cabe en timeout (`services/micro_portfolio_manager_service.py:330-369`). Esto tiende a comprar momentum ya movido, no pullbacks.

### 1.2 Stop, R y TP registrados al abrir

- En micro manager, cada linea propuesta usa `stop = price * 0.92` y `target = price * 1.16` (`services/micro_portfolio_manager_service.py:241-244`), y el `TradePick` guarda esos niveles (`services/micro_portfolio_manager_service.py:270-279`).
- Si un pick llega sin stop/TP, `AutoExecuteService` los reemplaza por stop -8% y TP +16% (`services/auto_execute_service.py:232-241`).
- El sizing de auto-execute calcula riesgo por accion contra ese stop y limita por presupuesto de riesgo/notional (`services/auto_execute_service.py:169-179`, `services/auto_execute_service.py:244-260`).
- La orden se manda como bracket GTC si es compra market con stop y TP (`services/alpaca_order_service.py:581-606`, payload bracket en `services/alpaca_order_service.py:790-797`).
- Al registrar fill, `PositionLifecycleService.register_from_fill` recalcula defaults si faltan: stop `entry * (1 - stop_pct)` y TP `entry * (1 + target_pct)` (`services/position_lifecycle_service.py:100-109`).
- Defaults de settings: stop 8%, TP 16%, micro time-stop 7d, stagnation 2d, trail micro 10%, arm +5%, Smart EOD 15:40 ET (`config/settings.py:223-260`).

**Definicion de R hoy:** `R = (entry_price - stop_loss) / entry_price`, normalmente 8%. TP 2R = +16% sobre entrada.

### 1.3 Stop efectivo despues de abrir: por que el journal puede quedar desfasado

Operaciones reales:

- El journal guarda `stop_loss` y `take_profit` de apertura (`database/models.py:320-329`) y calcula R multiple contra ese stop registrado al cerrar (`database/repositories/trade_journal_repository.py:110-130`).
- El lifecycle puede subir el stop efectivo: arma trailing solo cuando el pico >= entrada +5%, y entonces calcula `trail_stop = peak * (1 - trailing_pct)`; usa el maximo entre stop registrado y trail (`services/position_lifecycle_service.py:229-242`).
- El TP se evalua antes que stops de calendario (`services/position_lifecycle_service.py:252-259`).
- Stop/trailing se ejecuta si precio <= stop efectivo (`services/position_lifecycle_service.py:261-267`).
- Stagnation cierra solo verdes/flat: edad >= 2d y 0% <= PnL < 1.5%; rojos dentro del stop son "recuperacion", no stagnation (`services/position_lifecycle_service.py:277-299`).
- Time-stop ultimo recurso cierra si edad >= 7d y precio <= entrada * 0.995 (`services/position_lifecycle_service.py:301-312`).
- Smart EOD no cosecha verdes pequenos con `intraday_2r_hold_enabled`: carry de verdes hacia 2R y de rojos moderados; corta si perdida <= -8%, stop tocado, tesis invalidada o TP cercano/tocado (`services/intraday_flat_service.py:83-119`).

Implicacion para los 12 cierres: si solo miro `trade_journal.stop_loss/take_profit`, puedo subestimar el stop efectivo de una salida `Stop/trailing tocado` o no ver que EOD decidio carry. Para reconstruir cada trade real se necesitan:

1. journal open/close,
2. fills/activities de Alpaca,
3. ordenes reemplazadas/canceladas de stop GTC,
4. OHLC intradia durante la vida,
5. audit events `trailing_update`, `protective_stop_sync`, `intraday_flat`, `lifecycle_exit`.

En este entorno no hay credenciales ni DB, asi que esa reconstruccion real queda bloqueada.

## 2. Diagnostico de los 12 cierres reales

### 2.1 Disponibilidad de datos

Chequeo local:

- `DATABASE_URL`: ausente.
- `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`: ausentes.
- `data/*.db`: no existe en el checkout.

Por tanto, no puedo producir la tabla por cada uno de los 12 cierres con entrada, stop, R, TP, MFE/MAE y motivo real. El unico dato real disponible aqui viene del documento adjunto:

| Dato real disponible | Fuente |
|---|---|
| Equity 2026-09-30 aprox. $21.01 vs $21.74 depositados (-3.4%) | upload resumen, seccion 3 |
| 30 dias: 12 cierres, 0 TP 2R, 12/12 stagnation o stop | upload resumen, seccion 3 |
| PLUG 2026-09-10 stop aprox. -$0.54 | upload resumen, seccion 3 |
| BBAI 2026-09-28 stop aprox. -$0.45 | upload resumen, seccion 3 |
| SNAP seguia abierta al cierre del reporte | upload resumen, seccion 3 |

### 2.2 Plantilla de reconstruccion real

Cuando haya DB/Alpaca read-only, por cada trade se debe llenar:

| Campo | Fuente primaria | Nota de reconstruccion |
|---|---|---|
| entrada/fill | Alpaca activities `FILL` | journal puede usar fallback si no hubo fill_avg_price |
| stop/TP apertura | `trade_journal` / bracket order | no asumir que fue efectivo al cierre |
| stop efectivo | ordenes stop reemplazadas + audit `trailing_update` | trail arma tras +5%; micro trail 10% |
| MFE/MAE R | OHLC intradia entre fill open y fill close | usar stop efectivo por tramo si cambia |
| salida | Alpaca fill + `exit_reason` journal/audit | clasificar TP/stop/trail/time/EOD/stagnation |
| EOD/carry | audit `intraday_flat` + mandate thesis | Smart EOD puede mantener rojos/verdes |

## 3. Backtest reproducible

Codigo: `research/backtest/run_2r_backtest.py`.

### 3.1 Metodologia

- Datos: `yfinance`, OHLCV diario ajustado por splits.
- Universo: seed micro real del codigo + legacy solicitado para visibilidad. Tickers sin barras suficientes: BITF, MPW, NKLA, TWO, WISH.
- Sin look-ahead: senal con barra diaria cerrada; entrada en la siguiente apertura.
- Ejecucion: fraccional, notional dinamico `min($25, 35% equity)`, minimo $5, capital inicial $21.76.
- Costos/spread por lado: 75 bps si precio < $1; 35 bps si $1-$5; 25 bps si $5-$10; 15 bps si >= $10. Incluye spread+slippage; comision cero.
- Kill-switch Riesgo: 5% drawdown, umbral $20.672.
- Split: `2025-12-03` OOS. Ademas se corrio una cuenta OOS reiniciada desde $21.76 y con kill-stop activo.
- Limitacion: yfinance no garantiza supervivorship-free completo; delistados sin datos quedan marcados como no disponibles, no se imputan.

### 3.2 Variantes probadas

| Variante | Cambio vs actual |
|---|---|
| `actual_2R` | stop 8%, TP 16%, trail 10% armado +5%, stagnation 2d |
| `tp_1_5R` | TP 12% (1.5R) |
| `be_tras_1R` | mover stop a break-even tras MFE >= 1R |
| `adx25_2R` | exigir ADX >= 25 al entrar |
| `stagnation_4d` | stagnation 4d en vez de 2d |

### 3.3 Resultado OOS con cuenta real reiniciada

| Variante | Trades | Win % | Exp. R | Exp. %/trade | PF | Equity final | Retorno cuenta | Kill-switch OOS | Peor racha |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| actual_2R | 7 | 42.86 | -0.2710 | -2.1680 | 0.5351 | $20.5693 | -5.4720% | si, 2026-01-13 | 2 trades / -$1.2311 / -5.6467% |
| tp_1_5R | 7 | 42.86 | -0.3423 | -2.7386 | 0.4128 | $20.2969 | -6.7238% | si, 2026-01-13 | 2 trades / -$1.2147 / -5.6467% |
| be_tras_1R | 4 | 0.00 | -0.6437 | -5.1499 | 0.0000 | $20.2273 | -7.0437% | si, 2025-12-30 | 4 trades / -$1.5326 / -7.0437% |
| adx25_2R | 36 | 44.44 | -0.0504 | -0.4036 | 0.8566 | $20.4641 | -5.9554% | si, 2026-09-23 | 6 trades / -$1.8917 / -8.4614% |
| stagnation_4d | 27 | 37.04 | -0.0903 | -0.7229 | 0.7798 | $20.1248 | -7.5147% | si, 2026-09-22 | 5 trades / -$1.2317 / -5.7678% |

Distribucion de salidas OOS:

| Variante | Salidas |
|---|---|
| actual_2R | stop 4, stagnation 2, take_profit 1 |
| tp_1_5R | stop 4, stagnation 2, take_profit 1 |
| be_tras_1R | stop 2, stop_trailing 1, time_stop 1 |
| adx25_2R | stop_trailing 15, stop 9, stagnation 7, take_profit 3, time_stop 2 |
| stagnation_4d | stop 8, stop_trailing 7, stagnation 4, take_profit 4, time_stop 4 |

### 3.4 Lectura de MFE/MAE, ATR y entrada tardia

Medians OOS:

| Variante | MFE mediana | MAE mediana | TP / ATR mediano | Extension vs SMA20 mediana |
|---|---:|---:|---:|---:|
| actual_2R | 0.46R | -1.04R | 2.84 ATR | +7.23% |
| tp_1_5R | 0.46R | -1.04R | 2.13 ATR | +7.23% |
| be_tras_1R | 0.68R | -0.93R | 2.34 ATR | +13.02% |
| adx25_2R | 0.79R | -0.50R | 2.58 ATR | +7.40% |
| stagnation_4d | 0.80R | -0.74R | 2.56 ATR | +6.97% |

Interpretacion:

- En la variante actual, el trade mediano ni siquiera alcanza 1R de MFE. Apuntar a +16% requiere ~2.84 ATR medianos; para horizontes de dias, eso es exigente.
- Las entradas son momentum/extension: el propio codigo premia 1D/5D fuertes, volumen y precio sobre SMA20. La extension OOS mediana contra SMA20 fue ~7%. Eso apoya la hipotesis de entradas tardias.
- El stop de 8% no es "estrecho"; para la cuenta si es grande. Una perdida completa de una posicion de 35% del libro pierde ~2.8% de equity antes de costos. Dos stops casi activan kill-switch.
- Bajar TP a 1.5R no basta porque no corrige entradas ni rachas; empeoro expectancy OOS en esta muestra.
- ADX25 mejora la seleccion y retrasa el kill hasta septiembre, pero no cruza el umbral de aprobacion.

## 4. Recomendacion

### 4.1 No cambiar LIVE todavia

No recomiendo pasar ninguna de las variantes probadas a LIVE. Todas activan kill-switch OOS con la cuenta real simulada.

### 4.2 Cambio candidato para PAPER

Probar en PAPER una variante nueva (no implementada en LIVE):

1. Filtro de tendencia: ADX >= 25 o estructura HTF confirmada.
2. Filtro anti-chase: no entrar si `close > SMA20 * 1.07` o si 5D > +15% sin pullback intradia.
3. Objetivo dinamico: TP = min(1.5R, 1.8 ATR) y salida parcial/sintetica a 1R con trailing del resto.
4. Stop de riesgo: mantener stop maximo 8%, pero capear riesgo por trade a <= 1.5% del equity mientras el libro sea <$50.
5. Stagnation: no cerrar al dia 2 si MFE >= 0.75R; cerrar rapido si MFE < 0.25R y vuelve bajo VWAP/SMA corta.

Razon: el backtest sugiere que la mejora viene mas de filtrar entradas y normalizar por volatilidad que de mover solo el TP.

### 4.3 Plan de validacion PAPER antes de LIVE

Criterios minimos:

- Minimo 40 cierres PAPER o 20 sesiones con mercado abierto, lo que ocurra despues.
- Kill-switch simulado no activado.
- Expectancy >= +0.10R/trade y PF >= 1.15.
- Peor racha <= 3 perdidas consecutivas y perdida de racha <= 3% del equity.
- Al menos 25% de salidas por TP/parcial/trailing positivo, no dominadas por stop/stagnation.
- Comparar contra shadow actual_2R en paralelo; aprobar solo si supera actual_2R en expectancy y drawdown.

Si no cumple, mantener LIVE sin nuevos cambios de autonomia y volver a redisenar entradas/universo.

## 5. Archivos generados

- `research/backtest/run_2r_backtest.py`
- `research/backtest/output/trades.csv`
- `research/backtest/output/metrics.csv`
- `research/backtest/output/oos_account_trades.csv`
- `research/backtest/output/oos_account_metrics.csv`
- `research/backtest/output/metadata.json`
