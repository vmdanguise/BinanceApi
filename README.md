# Bot de Trading Blindado v4.7

Bot de trading **100% cuantitativo** para Binance.
Opera BTC/USDT y ETH/USDT con SL, trailing y cooldown dinamicos calculados
en vivo desde la volatilidad de los ultimos 7 dias.

---

## 1. Estrategia general

- **Activos:** `BTC/USDT` y `ETH/USDT`, evaluados **en paralelo** cada 60s.
- **Temporalidad:** velas de 1 hora (1H).
- **Senal de compra:** RSI fijo por moneda (BTC=30, ETH=40, configurable).
- **Seleccion de entrada:** scoring por relacion riesgo/recompensa (TP/SL), no solo SL.
- **Senal de venta TP:** 0.55% - 5% segun volatilidad (TP dinamico).
- **Senal de venta RSI:** RSI > 70 si la ganancia >= 0.35%.
- **Timeout:** vende a los 60 min si ganancia >= 0.35%; difiere si hay momento alcista.
- **Ejecucion:** compra como **maker** (LIMIT al bid); vende como **maker** (LIMIT al ask) con fallback a market; stop-loss usa market directo.
- **Tamano de orden:** `MIN_TRADE_USDT` fijo (siempre lo minimo rentable).
- **Filtro SMA 200:** no compra si el precio esta 5% bajo la media de 200h.

### Los 5 escudos defensivos

| # | Escudo | Que hace |
|---|--------|----------|
| 1 | **Cooldown Adaptativo Post-Perdida** | Stop-Loss tocado -> token congelado segun SL. |
| 2 | **Escudo Timeout 60min** | Posicion > 60min con ganancia >= 0.35% -> venta (difiere si hay momento alcista). |
| 3 | **Gating Anti-Saturacion** | Max 1 compra/hora por simbolo (ventas sin limite). |
| 4 | **Drawdown Diario** | Perdida del dia > 5% congela compras hasta el dia siguiente. |
| 5 | **SL Dinamico** | Calculado desde volatilidad 7d. |

---

## 2. Requisitos

- **Python 3.8+**
- Cuenta en **Testnet de Binance** (testnet.binance.vision, login con GitHub)
- Dependencias en `requirements.txt`

---

## 3. Instalacion

```powershell
# 1. Instalar dependencias
py -3 -m pip install -r requirements.txt

# 2. Conseguir claves de API
#    TESTNET: https://testnet.binance.vision (login con GitHub)
#    PRODUCCION: https://www.binance.com/es/my/settings/api-management

# 3. Configurar credenciales en conf/desa.env (testnet) o conf/prod.env (produccion)
```

El archivo `conf/desa.env` debe contener:

```
BINANCE_API_KEY=tu_api_key
BINANCE_SECRET_KEY=tu_secret_key
WS_BASE_URL=wss://stream.testnet.binance.vision
REST_BASE_URL=https://testnet.binance.vision
ENTORNO=testnet
```

Para produccion, crear `conf/prod.env` con las claves reales y `ENTORNO=prod`.

---

## 4. Entorno (ENVIRONMENT_SELECTED)

El entorno se define en `conf/bot.properties` mediante la clave `ENVIRONMENT_SELECTED`:

| Valor | Archivo cargado | Uso |
|-------|----------------|-----|
| `desa.env` (default) | `conf/desa.env` | Testnet / desarrollo |
| `prod.env` | `conf/prod.env` | Produccion (dinero real) |

```properties
# conf/bot.properties
ENVIRONMENT_SELECTED = desa.env
```

Para cambiar a produccion, editar esa linea a `ENVIRONMENT_SELECTED = prod.env`.

---

## 5. Uso

```powershell
# Arrancar el bot (carga conf/desa.env por defecto)
py -3 bot_trading.py

# Detener: Ctrl + C (guarda estado antes de salir)
```

### Background (servidor)

```bash
# Linux
./start_bot.sh
./status_bot.sh   # consultar estado
./stop_bot.sh     # detener

# Windows (doble click o terminal)
start_bot.bat
status_bot.bat
stop_bot.bat
```

### Consultar estado (sin conexion a Binance)

```bash
python cli.py
```

Salida:

```
  BOT TRADING v4.7  [TESTNET]  EN EJECUCION
  Capital: $326,062.80  P&L: +0.09  Trades hoy: 2  Inversion: $11.0
  Drawdown: 0.0%

  BTC/USDT
    Estado: LIQUIDO  RSI 50  SMA200 $64,077  EMA50 $65,626
    Entrada si RSI < 30  Cooldown: libre  vol 25.2%  SL 8.00%

  ETH/USDT
    Estado: EN POSICION  RSI 36  SMA200 $1,700  EMA50 $1,774
    Precio: $1,771.75  Entrada: $1,776.96  P&L: -0.29%
    Stop Loss: $1,634.80  Take Profit: $1,786.73  Invertido: $10.84  Horas: 0.3
    Cooldown: libre  vol 25.8%  SL 8.00%

  Ultimos eventos:
    [2026-06-17 13:51:43] [WS] Estado: conectado.
    [2026-06-17 13:51:44] [METRICAS] vol=25.18%  sl=8.00%
```

### Dashboard en vivo

El bot redibuja cada ciclo con capital, posiciones, escudos y eventos en tiempo real.

---

## 6. Logs

Rotacion automatica por tamano:
- `logs/bot_trading_YYYY-MM-DD.log`
- Si los logs suman > 100 MB, se borran los anteriores a 20 dias.
- Verificacion cada 1 hora.

---

## 7. Scripts auxiliares

| Script | Funcion |
|--------|---------|
| `cli.py` | Consulta rapida de estado (lee `estado_bot.json` + `metricas.json` + log). |
| `start_bot.sh` / `start_bot.bat` | Arranca en background con `ENVIRONMENT_SELECTED=desa.env`. |
| `stop_bot.sh` / `stop_bot.bat` | Detiene el bot. Si hay posiciones activas, pide confirmacion extra. |
| `status_bot.sh` / `status_bot.bat` | Estado via CLI con `ENVIRONMENT_SELECTED=desa.env`. |

---

## 8. Archivos del proyecto

| Archivo | Descripcion |
|---------|-------------|
| `bot_trading.py` | El bot completo. |
| `ws_manager.py` | WebSocket manager (precios, klines, balances). |
| `cli.py` | CLI de consulta de estado. |
| `metricas.json` | Snapshot de indicadores (RSI, SMA, EMA, volatilidad) actualizado cada ciclo. |
| `conf/desa.env` | Credenciales de testnet (default). |
| `conf/prod.env` | Referencia: credenciales de produccion. |
| `conf/bot.properties` | Configuracion unificada del bot. |
| `estado_bot.json` | Estado persistente del bot (posiciones, cooldown, drawdown). |
| `logs/` | Logs con rotacion diaria. |
| `requirements.txt` | Dependencias de Python. |

---

## 9. Evolucion

### v4.7 — Actual
- **ENVIRONMENT_SELECTED**: variable de entorno para elegir `conf/desa.env` o `conf/prod.env`
  (default `desa.env`). Elimina la necesidad de copiar/renombrar archivos `.env`.
- **RSI corregido**: cuando todas las velas son alcistas (loss=0), RSI=100 (antes daba 0).
- **Scoring de compra mejorado**: usa relacion TP/SL en vez de solo SL. Selecciona entradas
  con mejor riesgo/recompensa.
- **Min Notional**: `ejecutar_venta` usa el valor real desde la API de Binance antes de caer
  al fallback estatico.
- **Profit floor con TP dinamico**: `calcular_monto_orden` usa el TP real (dinamico o fijo)
  para el calculo del piso de ganancia minima.
- **Balance updates por WebSocket**: manejados eventos `balanceUpdate` (delta) ademas de
  `outboundAccountInfo` (snapshot).
- **Drawdown reseteado**: `drawdown_max_del_dia` se limpia al cambiar de dia.
- **Codigo muerto eliminado**: variable `METRICAS_KEYS` no utilizada, parametro `total_cuenta`
  sin uso, redundancia en calculo de BNB, variables RSI individuales convertidas a locales.

### v4.6 — Anterior
- TP dinamico + SL ajustado (Opcion 3)
- Recuperacion automatica de posiciones al arrancar
- Advertencia en stop_bot si hay posiciones activas
- Timeout 60min, RSI sobrecompra, ETH RSI entrada 30->40
- Config unificada `bot.properties`
- Dashboard con indicadores en vivo
- CLI de consulta (`cli.py`)
- Logs rotan por tamano
- Sandbox mode automatico segun URL

### v4.0 — Anterior
- TP 0.35%, timeout 30min, sin piso de ganancia.
- Config por entorno (`bot.desa.properties` / `bot.prod.properties`).
- Selector de entorno interactivo al arrancar.
- Sin CLI ni scripts de background.
- Control por teclado (r=rescatar, a=activar, s=reset).
- Dependencias `yfinance` y `requests` (no utilizadas).
