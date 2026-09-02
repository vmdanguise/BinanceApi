# -*- coding: utf-8 -*-
"""
===============================================================================
 BOT DE TRADING BLINDADO MULTI-TOKEN (V4.0)  -  BTC/USDT + ETH/USDT  -  1H
===============================================================================
 Estrategia 100% cuantitativa (sin IA). Disenado para correr en la TESTNET de
 Binance (dinero ficticio) durante 48-72hs antes de pasar a real.

 Opera BTC y ETH EN PARALELO (ambos se evaluan en cada ciclo de 60s).

    Implementa los 5 escudos defensivos:
      1. Cooldown Adaptativo Post-Perdida (SL × 50 horas, 0-6h)
      2. Escudo Timeout 60min (venta automatica estable)
      3. Gating Anti-Saturacion (1h SOLO compras)
      4. Drawdown Diario (5% = stop)
      5. SL Dinamico desde volatilidad 7d

 TP PROGRESIVO: (N+3) * fee_rate donde N = wins consecutivas por moneda.
 SL/TRAILING/COOLDOWN adaptativos desde volatilidad 7d.

  + Inversion MINIMA fija (siempre lo minimo rentable).
 + WebSocket Manager para datos en tiempo real.
 + Capa de Persistencia (estado_bot.json con ESCRITURA ATOMICA)

 BLINDAJES DE INFRAESTRUCTURA:
   - Escritura atomica del JSON (tmp + os.replace) -> evita corrupcion por corte.
   - Carga tolerante a JSON corrupto (backup + estado limpio de respaldo).
   - Reintentos con backoff ante caidas de red / errores transitorios de Binance.
   - Sincronizacion de reloj (adjustForTimeDifference + recvWindow amplio).
   - Manejo de Min Notional: venta de "polvo" se reporta, no rompe el bot.
   - Las ventas de rescate tienen prioridad absoluta.
================================================================================
"""

import os
import sys
import json
import math
import time
import shutil
import datetime
import traceback

import ccxt
import pandas as pd



from dotenv import load_dotenv

try:
    from ws_manager import WSManager
    WS_DISPONIBLE = True
except Exception:
    WS_DISPONIBLE = False

# ==========================================================================
# CARGA DE CONFIGURACION DESDE bot.properties
# ==========================================================================
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf", "bot.properties")


def _cfg_parse(raw):
    """Convierte el string crudo de una propiedad al tipo Python correcto."""
    v = raw.strip()
    if v.lower() == 'true':  return True
    if v.lower() == 'false': return False
    try:    return int(v)
    except ValueError: pass
    try:    return float(v)
    except ValueError: pass
    return v  # string


def cargar_config(path=_CONFIG_PATH):
    """
    Lee bot.properties (formato key = value, # = comentario).
    Devuelve un dict con todos los pares. Si el archivo no existe, usa
    los valores por defecto hardcodeados aqui como fallback de emergencia.
    """
    defaults = {
        "SYMBOL_LIST":             "BTC/USDT,ETH/USDT",
        "MIN_TRADE_USDT":          5.0,
        "MAX_TRADE_USDT":          100.0,
        "RSI_PERIODO":             14,
        "VOLATILIDAD_VENTANA_HORAS": 168,
        "VOLATILIDAD_MULT_SL":     0.3,
        "SL_DINAMICO_MIN":         0.005,
        "SL_DINAMICO_MAX":         0.03,
        "RSI_ENTRADA_BTC":         30,
        "RSI_ENTRADA_ETH":         40,
        "RSI_SOBRECOMPRA_BTC":     70,
        "RSI_SOBRECOMPRA_ETH":     70,
        "TP_PCT":                  0.0055,
        "TP_DINAMICO_HABILITADO":  True,
        "TP_VOL_MULT":             0.5,
        "TP_MIN_PCT":              0.01,
        "TP_MAX_PCT":              0.05,
        "MAX_DAILY_TRADES":        50,
        "TRAILING_ACTIVA_MIN":     0.001,
        "TRAILING_ACTIVA_MAX":     0.003,
        "TRAILING_DISTANCIA_MIN":  0.001,
        "TRAILING_DISTANCIA_MAX":  0.005,
        "COOLDOWN_MIN_HORAS":      0.0,
        "COOLDOWN_MAX_HORAS":      0.1,
        "SMA_DISTANCIA_MAX_PCT":   0.05,
        "SMA_FILTR0_HABILITADO":    True,
        "MIN_INTERVALO_MANIOBRAS_HORAS": 0.1,
        "DRAWDOWN_DIARIO_MAX_PCT": 0.05,
        "BINANCE_FEE_RATE":               0.001,
        "BINANCE_FEE_RATE_TAKER":         0.001,
        "MIN_NOTIONAL_FALLBACK":          10.0,
        "PROFIT_FLOOR_MULT":              1.0,
        "BNB_TARGET_PCT":                 0.05,
        "BNB_DIAS_COBERTURA":            60,

        "LOG_ARCHIVO_HABILITADO":         True,
        "LOG_ARCHIVO_PATH":               "logs/bot_trading.log",
        "LOG_BUFFER_MAX":                 4,
        "MAX_REINTENTOS":                 3,
        "BACKOFF_SEGUNDOS":               5,
        "LOOP_SLEEP_SEGUNDOS":            10,
        "LOG_MAX_MB":                     100,
        "LOG_RETENCION_DIAS":             20,
        "LOG_PURGA_INTERVALO_SEG":        3600,
    }

    cfg = dict(defaults)

    if not os.path.exists(path):
        print(f"[CONFIG] {path} no encontrado. Usando valores por defecto.", flush=True)
        return cfg

    with open(path, 'r', encoding='utf-8') as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith('#'):
                continue
            if '=' not in linea:
                continue
            clave, _, valor = linea.partition('=')
            clave = clave.strip()
            valor = valor.split('#')[0].strip()
            if clave in defaults:
                cfg[clave] = _cfg_parse(valor)

    print(f"[CONFIG] Configuracion cargada desde {path}", flush=True)
    return cfg


# Carga global de configuracion
_cfg = cargar_config()


def _init_config(path=None):
    """
    (Re)carga config desde path y actualiza todas las constantes globales.
    Se llama al importar el modulo (con bot.properties).
    """
    global _cfg
    global SYMBOL_LIST, MIN_TRADE_USDT, MAX_TRADE_USDT
    global RSI_PERIODO
    global VOLATILIDAD_VENTANA_HORAS, VOLATILIDAD_MULT_SL
    global SL_DINAMICO_MIN, SL_DINAMICO_MAX
    global RSI_ENTRADA
    global RSI_SOBRECOMPRA
    global TP_PCT
    global TP_DINAMICO_HABILITADO, TP_VOL_MULT, TP_MIN_PCT, TP_MAX_PCT
    global MAX_DAILY_TRADES
    global TRAILING_ACTIVA_MIN, TRAILING_ACTIVA_MAX, TRAILING_DISTANCIA_MIN, TRAILING_DISTANCIA_MAX
    global COOLDOWN_MIN_HORAS, COOLDOWN_MAX_HORAS
    global SMA_DISTANCIA_MAX_PCT
    global SMA_FILTR0_HABILITADO
    global MIN_INTERVALO_MANIOBRAS_HORAS
    global DRAWDOWN_DIARIO_MAX_PCT, BINANCE_FEE_RATE, BINANCE_FEE_RATE_TAKER
    global MIN_NOTIONAL_FALLBACK, PROFIT_FLOOR_MULT
    global BNB_TARGET_PCT, BNB_DIAS_COBERTURA


    global LOG_ARCHIVO_HABILITADO, LOG_ARCHIVO_PATH, LOG_BUFFER_MAX
    global LOG_MAX_MB, LOG_RETENCION_DIAS, LOG_PURGA_INTERVALO_SEG
    global MAX_REINTENTOS, BACKOFF_SEGUNDOS, LOOP_SLEEP_SEGUNDOS

    if path is not None:
        _cfg = cargar_config(path)
    else:
        _cfg = cargar_config()

    SYMBOL_LIST     = [s.strip() for s in str(_cfg["SYMBOL_LIST"]).split(",")]
    MIN_TRADE_USDT  = float(_cfg["MIN_TRADE_USDT"])
    MAX_TRADE_USDT  = float(_cfg["MAX_TRADE_USDT"])

    RSI_PERIODO               = int(_cfg["RSI_PERIODO"])
    VOLATILIDAD_VENTANA_HORAS  = int(_cfg["VOLATILIDAD_VENTANA_HORAS"])
    VOLATILIDAD_MULT_SL        = float(_cfg["VOLATILIDAD_MULT_SL"])
    SL_DINAMICO_MIN            = float(_cfg["SL_DINAMICO_MIN"])
    SL_DINAMICO_MAX            = float(_cfg["SL_DINAMICO_MAX"])
    RSI_ENTRADA_BTC            = float(_cfg["RSI_ENTRADA_BTC"])
    RSI_ENTRADA_ETH            = float(_cfg["RSI_ENTRADA_ETH"])
    RSI_ENTRADA                = {"BTC/USDT": RSI_ENTRADA_BTC, "ETH/USDT": RSI_ENTRADA_ETH}
    RSI_SOBRECOMPRA_BTC        = float(_cfg["RSI_SOBRECOMPRA_BTC"])
    RSI_SOBRECOMPRA_ETH        = float(_cfg["RSI_SOBRECOMPRA_ETH"])
    RSI_SOBRECOMPRA            = {"BTC/USDT": RSI_SOBRECOMPRA_BTC, "ETH/USDT": RSI_SOBRECOMPRA_ETH}
    TP_PCT                     = float(_cfg["TP_PCT"])
    TP_DINAMICO_HABILITADO     = str(_cfg.get("TP_DINAMICO_HABILITADO", "false")).strip().lower() in ("true", "1", "yes")
    TP_VOL_MULT                = float(_cfg.get("TP_VOL_MULT", 0.5))
    TP_MIN_PCT                 = float(_cfg.get("TP_MIN_PCT", 0.01))
    TP_MAX_PCT                 = float(_cfg.get("TP_MAX_PCT", 0.05))
    MAX_DAILY_TRADES           = int(_cfg["MAX_DAILY_TRADES"])

    TRAILING_ACTIVA_MIN      = float(_cfg["TRAILING_ACTIVA_MIN"])
    TRAILING_ACTIVA_MAX      = float(_cfg["TRAILING_ACTIVA_MAX"])
    TRAILING_DISTANCIA_MIN   = float(_cfg["TRAILING_DISTANCIA_MIN"])
    TRAILING_DISTANCIA_MAX   = float(_cfg["TRAILING_DISTANCIA_MAX"])
    COOLDOWN_MIN_HORAS       = float(_cfg["COOLDOWN_MIN_HORAS"])
    COOLDOWN_MAX_HORAS       = float(_cfg["COOLDOWN_MAX_HORAS"])

    SMA_DISTANCIA_MAX_PCT   = float(_cfg["SMA_DISTANCIA_MAX_PCT"])
    SMA_FILTR0_HABILITADO   = bool(_cfg["SMA_FILTR0_HABILITADO"])
    MIN_INTERVALO_MANIOBRAS_HORAS = float(_cfg["MIN_INTERVALO_MANIOBRAS_HORAS"])
    DRAWDOWN_DIARIO_MAX_PCT       = float(_cfg["DRAWDOWN_DIARIO_MAX_PCT"])

    BINANCE_FEE_RATE      = float(_cfg["BINANCE_FEE_RATE"])
    BINANCE_FEE_RATE_TAKER = float(_cfg["BINANCE_FEE_RATE_TAKER"])
    MIN_NOTIONAL_FALLBACK  = float(_cfg["MIN_NOTIONAL_FALLBACK"])
    PROFIT_FLOOR_MULT      = float(_cfg["PROFIT_FLOOR_MULT"])
    BNB_TARGET_PCT        = float(_cfg["BNB_TARGET_PCT"])
    BNB_DIAS_COBERTURA    = int(_cfg["BNB_DIAS_COBERTURA"])



    LOG_ARCHIVO_HABILITADO = bool(_cfg["LOG_ARCHIVO_HABILITADO"])
    LOG_ARCHIVO_PATH       = str(_cfg["LOG_ARCHIVO_PATH"])
    LOG_BUFFER_MAX         = int(_cfg["LOG_BUFFER_MAX"])
    LOG_MAX_MB             = int(_cfg["LOG_MAX_MB"])
    LOG_RETENCION_DIAS     = int(_cfg["LOG_RETENCION_DIAS"])
    LOG_PURGA_INTERVALO_SEG = int(_cfg["LOG_PURGA_INTERVALO_SEG"])

    MAX_REINTENTOS     = int(_cfg["MAX_REINTENTOS"])
    BACKOFF_SEGUNDOS   = int(_cfg["BACKOFF_SEGUNDOS"])
    LOOP_SLEEP_SEGUNDOS = int(_cfg["LOOP_SLEEP_SEGUNDOS"])


# Inicializar constantes globales desde bot.properties
_init_config()

# MIN_NOTIONAL_USDT se obtiene de la API de Binance (exchange.markets) al conectar.
MIN_NOTIONAL_USDT = None
_ULTIMA_ACT_REFRESH_NOTIONAL = 0.0
_INTERVALO_REFRESH_NOTIONAL = 1800  # 30 minutos
PROFIT_FLOOR_USDT = None


def _cargar_min_notional(exchange):
    """Extrae MIN_NOTIONAL_USDT de los mercados cargados de Binance y calcula PROFIT_FLOOR."""
    global MIN_NOTIONAL_USDT, PROFIT_FLOOR_USDT
    try:
        vals = []
        for sym in SYMBOL_LIST:
            m = exchange.markets.get(sym)
            if m and 'limits' in m and 'cost' in m['limits']:
                v = m['limits']['cost'].get('min')
                if v and v > 0:
                    vals.append(float(v))
        if vals:
            MIN_NOTIONAL_USDT = max(vals)
        else:
            MIN_NOTIONAL_USDT = MIN_NOTIONAL_FALLBACK
        PROFIT_FLOOR_USDT = MIN_NOTIONAL_USDT * BINANCE_FEE_RATE * PROFIT_FLOOR_MULT
        log(f"[CONFIG] MIN_NOTIONAL_USDT = ${MIN_NOTIONAL_USDT:.2f} (desde API Binance)")
        log(f"[CONFIG] PROFIT_FLOOR_USDT = ${PROFIT_FLOOR_USDT:.6f} (MIN_NOTIONAL x fee_rate x {PROFIT_FLOOR_MULT})")
    except Exception as e:
        MIN_NOTIONAL_USDT = MIN_NOTIONAL_FALLBACK
        PROFIT_FLOOR_USDT = MIN_NOTIONAL_USDT * BINANCE_FEE_RATE * PROFIT_FLOOR_MULT
        log(f"[CONFIG] Error leyendo MIN_NOTIONAL de API, fallback ${MIN_NOTIONAL_FALLBACK}: {e}")


def _limpiar_ordenes_abiertas(exchange, estado_bot):
    """
    Verifica y limpia ordenes abiertas en Binance al arrancar.
    Cancela TODAS las ordenes pendientes (buy y sell) para re-evaluar.
    Si hay saldo de activo sin posicion registrada y supera MIN_NOTIONAL,
    recupera la posicion con SL/TP fresco para que el bot la gestione.
    """
    log("[LIMPIEZA] Verificando ordenes abiertas en Binance...")

    # 1. Cancelar TODAS las ordenes pendientes (buy y sell)
    for symbol in SYMBOL_LIST:
        try:
            ok, open_orders = _con_reintentos(
                lambda s=symbol: exchange.fetch_open_orders(s),
                f"open_orders {symbol}"
            )
            if ok and open_orders:
                for order in open_orders:
                    order_id = order.get('id')
                    side = order.get('side', '')
                    log(f"[LIMPIEZA] Cancelando orden {side} pendiente: {order_id} {symbol}.")
                    _con_reintentos(
                        lambda oid=order_id, s=symbol: exchange.cancel_order(oid, s),
                        f"cancel {symbol}"
                    )
                    log(f"[LIMPIEZA] Orden {order_id} cancelada.")
        except Exception as e:
            log(f"[LIMPIEZA] Error verificando ordenes de {symbol}: {e}")

    # 2. Detectar posiciones huerfanas y recuperarlas con SL/TP
    try:
        ok, balance = _con_reintentos(lambda: exchange.fetch_balance(), "balance limpieza")
        if ok and balance:
            free = balance.get('free', {})
            used = balance.get('used', {})
            ahora = datetime.datetime.now()
            for symbol in SYMBOL_LIST:
                base = symbol.split('/')[0]
                cantidad_total = float(used.get(base, 0) or 0) + float(free.get(base, 0) or 0)
                posicion_estado = estado_bot["posiciones"].get(symbol)

                if posicion_estado is None and cantidad_total > 0:
                    precio = 0.0
                    ok_tk, tk = _con_reintentos(
                        lambda s=symbol: exchange.fetch_ticker(s), f"ticker {symbol}")
                    if ok_tk and tk:
                        precio = float(tk.get('last', 0) or 0)
                    valor = cantidad_total * precio

                    mn = MIN_NOTIONAL_FALLBACK
                    if valor >= mn:
                        sl_pct = 0.02
                        monto = round(cantidad_total * precio, 2)
                        estado_bot["posiciones"][symbol] = {
                            "precio_compra": precio,
                            "cantidad": cantidad_total,
                            "monto_invertido": monto,
                            "stop_loss": precio * (1 - sl_pct),
                            "take_profit": precio * (1 + TP_PCT),
                            "fecha_entrada": ahora.isoformat(),
                            "trailing_activa_pct": TRAILING_ACTIVA_MIN,
                            "trailing_distancia_pct": TRAILING_DISTANCIA_MIN,
                            "cooldown_horas": COOLDOWN_MIN_HORAS,
                        }
                        estado_bot["ultima_maniobra_time"][symbol] = ahora.isoformat()
                        log(f"[LIMPIEZA] POSICION RECUPERADA: {cantidad_total:.6f} {base} "
                            f"(~${monto:.2f}). SL/TP asignados con datos actuales.")
                    else:
                        log(f"[LIMPIEZA] Polvo no registrado: {cantidad_total:.6f} {base} "
                            f"(~${valor:.2f}). Minimo ${mn:.2f}.")
    except Exception as e:
        log(f"[LIMPIEZA] Error verificando posiciones: {e}")

    guardar_estado(estado_bot)
    log("[LIMPIEZA] Verificacion completada.")


def _calcular_metricas_adaptativas(df):
    """
    Calcula SL, trailing, TP dinámico y cooldown basados en la
    volatilidad de los ultimos 7 dias.
    Devuelve dict con las metricas o None si no hay datos suficientes.
    """
    if df is None or len(df) < VOLATILIDAD_VENTANA_HORAS:
        return None

    ventana = df.tail(VOLATILIDAD_VENTANA_HORAS)
    dias = []
    for i in range(0, len(ventana), 24):
        bloque = ventana.iloc[i:i+24]
        if len(bloque) < 6:
            continue
        high_dia = bloque['high'].max()
        low_dia = bloque['low'].min()
        rango = (high_dia - low_dia) / low_dia
        dias.append(rango)
    if len(dias) < 3:
        return None

    vol = sum(dias) / len(dias)

    sl = max(SL_DINAMICO_MIN, min(vol * VOLATILIDAD_MULT_SL, SL_DINAMICO_MAX))
    t_act = max(TRAILING_ACTIVA_MIN, min(vol * 0.4, TRAILING_ACTIVA_MAX))
    t_dist = max(TRAILING_DISTANCIA_MIN, min(vol * 0.6, TRAILING_DISTANCIA_MAX))
    cd = max(COOLDOWN_MIN_HORAS, min(sl * 50, COOLDOWN_MAX_HORAS))
    tp = max(TP_MIN_PCT, min(vol * TP_VOL_MULT, TP_MAX_PCT)) if TP_DINAMICO_HABILITADO else TP_PCT

    log(f"[METRICAS] vol={vol*100:.2f}%  sl={sl*100:.2f}%  tp={tp*100:.2f}%")
    return {
        "vol": vol, "sl": sl,
        "t_act": t_act, "t_dist": t_dist, "cd": cd,
        "tp": tp,
    }


def _calcular_objetivo_bnb():
    """
    Calcula el objetivo de reserva de BNB en USDT.
    Usa MIN_TRADE_USDT como base (inversion minima fija).
    """
    mn = MIN_NOTIONAL_FALLBACK
    if BNB_DIAS_COBERTURA > 0:
        daily_volume = MIN_TRADE_USDT * MAX_DAILY_TRADES
        daily_fee = daily_volume * BINANCE_FEE_RATE
        objetivo = daily_fee * BNB_DIAS_COBERTURA
        return max(objetivo, mn)
    return max(MIN_TRADE_USDT * BNB_TARGET_PCT, mn)


# ==========================================================================
# CONSTANTES OPERATIVAS (leidas desde bot.properties)
# ==========================================================================

_PATH_ESTADO         = "estado_bot.json"
_PATH_ESTADO_TMP     = "estado_bot.tmp.json"
_PATH_ESTADO_BAK     = "estado_bot.corrupto.bak"

# Variables de path que pueden cambiar segun entorno (prod -> *_prod.*)
PATH_ESTADO     = _PATH_ESTADO
PATH_ESTADO_TMP = _PATH_ESTADO_TMP
PATH_ESTADO_BAK = _PATH_ESTADO_BAK



# Ultimas metricas adaptativas calculadas (para el dashboard)
ULTIMAS_METRICAS = {}

# WebSocket manager global
ws_manager = None



# ==========================================================================
# UTILIDADES DE LOG (con buffer para el dashboard)
# ==========================================================================
_LOG_BUFFER = []          # ultimos eventos para mostrar en el dashboard
# LOG_BUFFER_MAX viene de bot.properties


def log(msg):
    """
    Imprime con timestamp, guarda en el buffer del dashboard y, si
    LOG_ARCHIVO_HABILITADO=true, escribe en el archivo de log rotativo.
    """
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    linea = f"[{ts}] {msg}"

    # Buffer en memoria para el dashboard
    _LOG_BUFFER.append(linea)
    if len(_LOG_BUFFER) > LOG_BUFFER_MAX:
        _LOG_BUFFER.pop(0)

    # Escritura en archivo (si esta habilitada)
    if LOG_ARCHIVO_HABILITADO:
        try:
            with open(LOG_ARCHIVO_PATH, 'a', encoding='utf-8') as f:
                f.write(linea + '\n')
        except Exception as e:
            # No romper el bot por un fallo de escritura de log
            print(f"[LOG-ERROR] No se pudo escribir en {LOG_ARCHIVO_PATH}: {e}", flush=True)

    # Siempre mostrar en terminal
    print(linea, flush=True)


_ULTIMA_PURGA_LOG = 0.0


def _purgar_logs():
    """Purga archivos de log viejos si el total supera LOG_MAX_MB."""
    global _ULTIMA_PURGA_LOG
    ahora = time.time()
    if ahora - _ULTIMA_PURGA_LOG < LOG_PURGA_INTERVALO_SEG:
        return
    _ULTIMA_PURGA_LOG = ahora

    if not LOG_ARCHIVO_HABILITADO:
        return

    log_dir = os.path.dirname(LOG_ARCHIVO_PATH) or "."
    if not os.path.isdir(log_dir):
        return

    try:
        archivos = []
        for f in os.listdir(log_dir):
            if not f.endswith('.log'):
                continue
            ruta = os.path.join(log_dir, f)
            if not os.path.isfile(ruta):
                continue
            archivos.append((ruta, os.path.getsize(ruta), f))

        total_mb = sum(sz for _, sz, _ in archivos) / (1024 * 1024)
        if total_mb <= LOG_MAX_MB:
            return

        archivos.sort(key=lambda x: x[2], reverse=True)
        desde = ahora - LOG_RETENCION_DIAS * 86400
        eliminados = 0
        liberado = 0
        for ruta, sz, nombre in archivos:
            try:
                fecha_str = nombre.replace("bot_trading_", "").replace(".log", "")
                fecha = datetime.datetime.strptime(fecha_str, "%Y-%m-%d").timestamp()
            except (ValueError, IndexError):
                continue
            if fecha >= desde:
                continue
            os.remove(ruta)
            eliminados += 1
            liberado += sz

        if eliminados:
            log(f"[LOGS] Purga: {eliminados} archivo(s) eliminados, {liberado/(1024*1024):.2f} MB liberados. Total actual: {sum(os.path.getsize(os.path.join(log_dir, f)) for f in os.listdir(log_dir) if f.endswith('.log') and os.path.isfile(os.path.join(log_dir, f)))/(1024*1024):.2f} MB.")
    except Exception as e:
        log(f"[LOG-ERROR] No se pudo purgar logs: {e}")


_METRICAS_JSON_PATH = "metricas.json"


def _guardar_metricas(total_cuenta=None):
    """Persiste ULTIMAS_METRICAS + MIN_TRADE_USDT + total_cuenta a metricas.json para cli.py."""
    try:
        data = dict(ULTIMAS_METRICAS)
        data["_min_trade_usdt"] = MIN_TRADE_USDT
        data["_min_notional_usdt"] = MIN_NOTIONAL_USDT
        data["_profit_floor_usdt"] = PROFIT_FLOOR_USDT
        if total_cuenta is not None:
            data["_total_cuenta"] = total_cuenta
        with open(_METRICAS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        log(f"[METRICAS] No se pudo guardar metricas.json: {e}")


# ==========================================================================
# GESTION DE PERSISTENCIA (JSON CON ESCRITURA ATOMICA)  -> Riesgo #1
# ==========================================================================
def _estado_inicial():
    """Estado por defecto. Las maniobras se inician 'ayer' para poder operar ya."""
    ahora_atras = datetime.datetime.now() - datetime.timedelta(days=1)
    return {
        "trades_executed_today": 0,
        "ultima_limpieza_diaria": datetime.datetime.now().isoformat(),
        "cooldown_until": {sym: None for sym in SYMBOL_LIST},
        "ultima_maniobra_time": {sym: ahora_atras.isoformat() for sym in SYMBOL_LIST},
        "posiciones": {sym: None for sym in SYMBOL_LIST},
        "capital_inicio_dia": None,
        "balance_trades": 0.0,
    }


def _parse_dt(valor):
    """Convierte string ISO a datetime; tolera None y datetime ya parseado."""
    if valor is None:
        return None
    if isinstance(valor, datetime.datetime):
        return valor
    return datetime.datetime.fromisoformat(str(valor))


def cargar_estado():
    """
    Carga el estado desde disco. Si el JSON esta corrupto (corte de luz al
    escribir), respalda el archivo danado y arranca con estado limpio en vez
    de morir con JSONDecodeError.
    """
    if not os.path.exists(PATH_ESTADO):
        log("No existe estado previo. Inicializando memoria limpia.")
        estado = _estado_inicial()
        guardar_estado(estado)
        return estado

    try:
        with open(PATH_ESTADO, 'r', encoding='utf-8') as f:
            estado = json.load(f)
    except (json.JSONDecodeError, ValueError) as e:
        log(f"[ALERTA] estado_bot.json corrupto ({e}). Respaldando y reiniciando memoria.")
        try:
            shutil.copy2(PATH_ESTADO, PATH_ESTADO_BAK)
        except Exception:
            pass
        estado = _estado_inicial()
        guardar_estado(estado)
        return estado

    # Normalizar claves que pudieron faltar si el archivo es de una version vieja
    base = _estado_inicial()
    for k, v in base.items():
        estado.setdefault(k, v)
    for sym in SYMBOL_LIST:
        estado["cooldown_until"].setdefault(sym, None)
        estado["ultima_maniobra_time"].setdefault(sym, base["ultima_maniobra_time"][sym])
        estado["posiciones"].setdefault(sym, None)

    return estado


def guardar_estado(estado):
    """
    Escritura ATOMICA: vuelca a un archivo temporal, hace flush + fsync y luego
    os.replace (renombrado atomico). Asi el JSON principal nunca queda a medias.
    """
    copia = json.loads(json.dumps(estado, default=str))
    try:
        with open(PATH_ESTADO_TMP, 'w', encoding='utf-8') as f:
            json.dump(copia, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(PATH_ESTADO_TMP, PATH_ESTADO)
    except Exception as e:
        log(f"[ALERTA] No se pudo guardar el estado: {e}")


# ==========================================================================
# CONEXION E INDICADORES FINANCIEROS
# ==========================================================================
def inicializar_exchange():
    """
    Crea el cliente CCXT contra Binance.
    Usa set_sandbox_mode si la REST_BASE_URL del .env apunta a testnet.
    """
    api_key = os.getenv('BINANCE_API_KEY')
    secret = os.getenv('BINANCE_SECRET_KEY')

    if not api_key or not secret:
        log("[ERROR FATAL] Faltan BINANCE_API_KEY / BINANCE_SECRET_KEY en las variables de entorno.")
        raise SystemExit(1)

    rest_base = os.getenv("REST_BASE_URL")
    if not rest_base:
        log("[ERROR FATAL] Falta REST_BASE_URL en el archivo .env.")
        log("Ejemplo: REST_BASE_URL=https://api.binance.com  (o https://testnet.binance.vision)")
        raise SystemExit(1)

    es_testnet = "testnet" in rest_base.lower()

    exchange = ccxt.binance({
        'apiKey': api_key,
        'secret': secret,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'spot',
            'adjustForTimeDifference': True,
            'recvWindow': 10000,
        },
    })

    if es_testnet:
        exchange.set_sandbox_mode(True)
        log("[SISTEMA] Sandbox activado: operando sobre TESTNET.")
    else:
        exchange.urls['api']['public'] = f'{rest_base}/api/v3'
        exchange.urls['api']['private'] = f'{rest_base}/api/v3'

    exchange.load_markets()
    _cargar_min_notional(exchange)

    log(f"[SISTEMA] Conectado a Binance via {rest_base}.")

    # Validar que MIN_TRADE_USDT supera el NOTIONAL filter de Binance
    if MIN_NOTIONAL_USDT and MIN_TRADE_USDT < MIN_NOTIONAL_USDT:
        log(f"[ERROR FATAL] MIN_TRADE_USDT=${MIN_TRADE_USDT:.2f} es menor al "
            f"MIN_NOTIONAL de Binance=${MIN_NOTIONAL_USDT:.2f}.")
        log(f"Modifica MIN_TRADE_USDT en conf/bot.desa.properties a ${MIN_NOTIONAL_USDT:.2f} "
            f"o mas y vuelve a arrancar.")
        log(f"Sin este ajuste, TODAS las ordenes seran rechazadas por Binance.")
        sys.exit(1)

    return exchange


def _con_reintentos(funcion, descripcion):
    """
    Ejecuta una llamada de red reintentando ante errores transitorios
    (red caida, 5xx, timeouts). Riesgos #2, #5 y cortes de internet.
    Devuelve (ok, resultado). No reintenta errores de logica/saldo.
    """
    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            return True, funcion()
        except (ccxt.NetworkError, ccxt.RequestTimeout, ccxt.ExchangeNotAvailable,
                ccxt.DDoSProtection) as e:
            espera = BACKOFF_SEGUNDOS * intento
            log(f"[RED] {descripcion} fallo (intento {intento}/{MAX_REINTENTOS}): {e}. Reintento en {espera}s.")
            time.sleep(espera)
        except ccxt.RateLimitExceeded as e:
            espera = 60 * intento
            log(f"[RATE-LIMIT] {descripcion} rate limit excedido (intento {intento}/{MAX_REINTENTOS}). "
                f"Esperando {espera}s antes de reintentar.")
            time.sleep(espera)
        except ccxt.BaseError as e:
            # Error de exchange no transitorio (p.ej. saldo, parametros): no reintentar
            log(f"[EXCHANGE] {descripcion} rechazado: {e}")
            return False, e
    log(f"[RED] {descripcion} agoto los reintentos. Se omite este ciclo.")
    return False, None


def obtener_indicadores_tecnicos(exchange, symbol):
    """
    Calcula RSI, ATR%, SMA 200 y metricas adaptativas sobre velas de 1H.
    Devuelve (rsi, atr_pct, close, sma200, ema50, metricas).
    metricas es un dict de _calcular_metricas_adaptativas() o None.
    Usa WS klines si esta disponible, sino fallback a REST.
    """
    candles = None

    # Intentar desde WS cache
    if ws_manager and ws_manager.is_connected():
        ws_klines = ws_manager.get_klines(symbol, limit=210)
        if ws_klines and len(ws_klines) >= 50:
            candles = [[k["t"], k["o"], k["h"], k["l"], k["c"], k["v"]] for k in ws_klines]

    # Fallback a REST
    if not candles:
        ok, candles = _con_reintentos(
            lambda: exchange.fetch_ohlcv(symbol, timeframe='1h', limit=210),
            f"fetch_ohlcv {symbol}",
        )
        if not ok or not candles:
            return None, None, None, None, None, None

    try:
        df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

        # --- RSI (Wilder/SMA simple) ---
        delta = df['close'].diff()
        gain = delta.clip(lower=0).rolling(window=RSI_PERIODO).mean()
        loss = (-delta.clip(upper=0)).rolling(window=RSI_PERIODO).mean()
        # RSI Standard: si loss=0 (solo subidas) RSI=100; si gain=loss=0 (sin cambios) RSI=50
        rs = gain / loss
        sin_perdidas = (loss == 0) & (gain > 0)
        sin_cambios  = (gain == 0) & (loss == 0)
        rs = rs.where(~sin_perdidas, float('inf'))
        rs = rs.where(~sin_cambios, 1.0)
        df['rsi'] = 100 - (100 / (1 + rs))

        # --- ATR Porcentual ---
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(window=14).mean()
        df['atr_pct'] = (atr / df['close']) * 100

        # --- SMA 200 + EMA 50 (Filtro Anticuchillo) ---
        df['sma200'] = df['close'].rolling(window=200).mean()
        df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()

        rsi    = float(df['rsi'].iloc[-1])
        atr_pct = float(df['atr_pct'].iloc[-1])
        close  = float(df['close'].iloc[-1])
        sma200_raw = df['sma200'].iloc[-1]
        sma200 = float(sma200_raw) if not pd.isna(sma200_raw) else None
        ema50_raw = df['ema50'].iloc[-1]
        ema50 = float(ema50_raw) if not pd.isna(ema50_raw) else None

        # --- Metricas adaptativas (SL, TP, RSI, trailing, cooldown) ---
        metricas = _calcular_metricas_adaptativas(df)
        # Anadir senal de momento alcista para el filtro de TP
        if metricas and len(df) >= 5:
            closes = df['close'].iloc[-5:-1]
            metricas['momento_alcista'] = all(closes.iloc[i] > closes.iloc[i-1] for i in range(1, 4))

        if pd.isna(rsi) or pd.isna(atr_pct) or pd.isna(close):
            return None, None, None, None, None, None
        return rsi, atr_pct, close, sma200, ema50, metricas
    except Exception as e:
        log(f"[ERROR INDICADORES - {symbol}]: {e}")
        return None, None, None, None, None, None





# ==========================================================================
# EJECUCION DE ORDENES (TESTNET REAL) CON MANEJO DE MIN NOTIONAL
# ==========================================================================
def obtener_saldo_usdt(exchange):
    """Devuelve el USDT libre real de la cuenta (o None si falla la consulta)."""
    if ws_manager and ws_manager.is_connected():
        ws_bal = ws_manager.get_balance('USDT')
        if ws_bal > 0:
            return ws_bal
    ok, balance = _con_reintentos(lambda: exchange.fetch_balance(), "balance USDT")
    if not ok or not balance:
        return None
    try:
        return float(balance.get('free', {}).get('USDT', 0.0) or 0.0)
    except Exception:
        return None


def calcular_capital_total(exchange, estado_bot):
    """
    Calcula y devuelve un dict con:
      - total_cuenta  : USDT libres + valor de mercado de TODOS los activos
                        (BTC, ETH, BNB) que estan en la cuenta de Binance.
                        Es la foto real de cuanto vale tu cuenta ahora mismo.
      - disponible    : USDT libres (lo que podes usar para nuevas compras).
      - en_posiciones : valor de mercado de las posiciones abiertas del bot.
    Devuelve None en todos los campos si Binance no responde.
    Tambien retorna free (dict de saldos libres) y bnb_precio para evitar
    que el dashboard duplique llamadas API.
    """
    ok, balance = _con_reintentos(lambda: exchange.fetch_balance(), "capital total")
    if not ok or not balance:
        return {"total_cuenta": None, "disponible": None,
                "en_posiciones": None}

    free  = balance.get('free',  {})
    total = balance.get('total', {})  # libre + bloqueado en ordenes abiertas

    usdt_libre = float(free.get('USDT',  0) or 0)
    usdt_total = float(total.get('USDT', 0) or 0)

    valor_activos = 0.0
    for sym in SYMBOL_LIST:
        base = sym.split('/')[0]
        cantidad = float(total.get(base, 0) or 0)
        if cantidad > 0:
            precio = 0.0
            if ws_manager and ws_manager.is_connected():
                precio = ws_manager.get_price(sym)
            if precio <= 0:
                ok_tk, ticker = _con_reintentos(
                    lambda s=sym: exchange.fetch_ticker(s), f"ticker {sym}")
                if ok_tk and ticker:
                    precio = float(ticker.get('last', 0) or 0)
            valor_activos += cantidad * precio

    bnb_precio = 0.0
    bnb_cant = float(total.get('BNB', 0) or 0)
    if bnb_cant > 0:
        if ws_manager and ws_manager.is_connected():
            bnb_precio = ws_manager.get_price('BNB/USDT')
        if bnb_precio <= 0:
            ok_bnb, tk_bnb = _con_reintentos(
                lambda: exchange.fetch_ticker('BNB/USDT'), "ticker BNB")
            if ok_bnb and tk_bnb:
                bnb_precio = float(tk_bnb.get('last', 0) or 0)
        valor_activos += bnb_cant * bnb_precio

    total_cuenta = usdt_total + valor_activos

    # valor solo de las posiciones que el bot tiene abiertas
    en_pos = 0.0
    for sym, pos in estado_bot.get("posiciones", {}).items():
        if pos:
            monto = pos.get("monto_invertido", 0)
            en_pos += float(monto)

    return {
        "total_cuenta":  round(total_cuenta, 2),
        "disponible":    round(usdt_libre, 2),
        "en_posiciones": round(en_pos, 2),
        "free":          free,
        "bnb_precio":    bnb_precio,
    }


def calcular_monto_orden(exchange, symbol=None, tp_pct=None):
    """
    Calcula cuanto USDT invertir: MIN_TRADE_USDT fijo (inversion minima).
    Si la ganancia esperada no cubre el piso minimo, escala el monto.
    Restricciones duras:
      - Nunca menor al minimo rentable (monto * (TP - fees) >= PROFIT_FLOOR)
      - Nunca mayor al MAX_TRADE_USDT
    Devuelve (monto, saldo) o (None, saldo) si no alcanza el minimo.
    """
    saldo = obtener_saldo_usdt(exchange)
    if saldo is None:
        log("[SALDO] No se pudo leer USDT real de Binance. Compra abortada.")
        return None, None

    monto = MIN_TRADE_USDT
    tp_efectivo = tp_pct if tp_pct is not None else TP_PCT

    # RESTRICCION: minimo rentable (ganancia >= PROFIT_FLOOR despues de fees)
    if symbol:
        ganancia_neta = monto * (tp_efectivo - 2 * BINANCE_FEE_RATE)
        min_profit = PROFIT_FLOOR_USDT if PROFIT_FLOOR_USDT else MIN_TRADE_USDT * BINANCE_FEE_RATE
        if ganancia_neta < min_profit and tp_efectivo > 2 * BINANCE_FEE_RATE:
            monto_necesario = min_profit / (tp_efectivo - 2 * BINANCE_FEE_RATE)
            monto = max(monto, monto_necesario)

    monto = max(MIN_TRADE_USDT, min(monto, MAX_TRADE_USDT))

    if saldo < MIN_TRADE_USDT or monto > saldo:
        return None, saldo

    return monto, saldo


def _formatear_fee(order, lado="", monto=0.0, precio=0.0):
    """
    Extrae la comision real de la orden CCXT.
    Si es 0 (testnet), simula la fee segun configuracion.
    `lado`: 'compra' o 'venta', `monto`: monto en USD invertido, `precio`: precio del activo.
    """
    fee = order.get('fee') or {}
    cost = fee.get('cost')
    currency = fee.get('currency', '')
    if cost is not None and float(cost) > 0:
        return f"Fee: {float(cost):.6f} {currency}"

    if lado == "venta" and monto > 0 and precio > 0:
        sim = monto * precio * BINANCE_FEE_RATE_TAKER
        return f"Fee: ${sim:.4f} ({BINANCE_FEE_RATE_TAKER*100:.2f}% taker simulado)"
    if lado == "compra" and monto > 0:
        sim = monto * BINANCE_FEE_RATE
        return f"Fee: ${sim:.4f} ({BINANCE_FEE_RATE*100:.3f}% maker simulado)"
    return "Fee: $0 (testnet)"

def ejecutar_compra(exchange, symbol, precio_actual, monto_usdt):
    """
    Compra como MAKER (limit al bid price) para pagar menos fee.
    Espera hasta 45s a que se llene. Si no, cancela y devuelve False.
    Devuelve (ok, cantidad_base_comprada, fee_str, precio_fill_real).
    """
    # Calcular cantidad base a comprar
    cantidad = monto_usdt / precio_actual
    try:
        cantidad = float(exchange.amount_to_precision(symbol, cantidad))
    except Exception:
        pass
    # Si amount_to_precision redondeó hacia abajo y el notional queda bajo, recalcular ceiling al stepSize
    if 0 < cantidad * precio_actual < MIN_NOTIONAL_USDT:
        try:
            step = exchange.market(symbol)['precision']['amount']
            if step and step > 0:
                cantidad = math.ceil(MIN_NOTIONAL_USDT / precio_actual / step) * step
        except Exception:
            pass
    if cantidad <= 0:
        return False, 0.0, "", 0.0

    # Obtener bid price del orderbook para colocar una orden maker
    precio_orden = precio_actual
    ok_ob, ob = _con_reintentos(lambda: exchange.fetch_order_book(symbol, 5), f"orderbook {symbol}")
    if ok_ob and ob and ob.get('bids'):
        bid_price = float(ob['bids'][0][0])
        if bid_price > 0:
            precio_orden = bid_price

    # Formatear precio segun precision del mercado (evita PRICE_FILTER de Binance)
    try:
        precio_orden = float(exchange.price_to_precision(symbol, precio_orden))
    except Exception:
        pass

    # Colocar LIMIT BUY (maker)
    ok, order = _con_reintentos(
        lambda: exchange.create_order(symbol, 'limit', 'buy', cantidad, precio_orden),
        f"LIMIT BUY {symbol}",
    )
    if not ok or order is None:
        log(f"[COMPRA] {symbol}: orden LIMIT rechazada al crearla.")
        return False, 0.0, "", 0.0

    order_id = order.get('id')
    if not order_id:
        return False, 0.0, "", 0.0

    # Esperar hasta 30s a que se llene usando WS order updates
    fin = time.time() + 30
    while time.time() < fin:
        time.sleep(1)

        # Buscar update en el WS manager
        if ws_manager and ws_manager.is_connected():
            updates = ws_manager.pop_order_updates()
            for u in updates:
                if u.get("order_id") == order_id:
                    if u["status"] == "FILLED":
                        filled = u["executed_qty"]
                        cost = u["cummulative_quote"]
                        precio_fill = (cost / filled) if filled > 0 and cost > 0 else precio_orden
                        log(f"[COMPRA MAKER OK] {symbol} | {filled} unidades a ${precio_fill:.2f} "
                            f"(${filled * precio_fill:.2f})")
                        return True, filled, "", precio_fill
                    if u["status"] in ("CANCELED", "EXPIRED"):
                        log(f"[COMPRA] {symbol}: orden LIMIT {u['status'].lower()}.")
                        return False, 0.0, "", 0.0

        # Fallback: polling REST cada 5s
        elapsed = time.time() - (fin - 30)
        if int(elapsed) % 5 == 0 and int(elapsed) > 0:
            ok_chk, chk = _con_reintentos(
                lambda: exchange.fetch_order(order_id, symbol),
                f"check order {symbol}",
            )
            if ok_chk and chk:
                status = chk.get('status', '')
                filled = float(chk.get('filled', 0) or 0)
                if status == 'closed':
                    cost = float(chk.get('cost', 0) or 0)
                    precio_fill = (cost / filled) if filled > 0 and cost > 0 else precio_orden
                    log(f"[COMPRA MAKER OK] {symbol} | {filled} unidades a ${precio_fill:.2f} "
                        f"(${filled * precio_fill:.2f})")
                    return True, filled, _formatear_fee(chk, "compra", monto_usdt), precio_fill
                if status in ('canceled', 'expired'):
                    log(f"[COMPRA] {symbol}: orden LIMIT {status}.")
                    return False, 0.0, "", 0.0

    # Timeout: cancelar la orden
    log(f"[COMPRA] {symbol}: orden LIMIT no se lleno en 30s. Cancelando.")
    _con_reintentos(lambda: exchange.cancel_order(order_id, symbol), f"cancel order {symbol}")

    # Verificar si hubo fill parcial
    ok_final, order_final = _con_reintentos(
        lambda: exchange.fetch_order(order_id, symbol),
        f"final check {symbol}",
    )
    if ok_final and order_final:
        filled_final = float(order_final.get('filled', 0) or 0)
        if filled_final > 0:
            cost_final = float(order_final.get('cost', 0) or 0)
            precio_fill = (cost_final / filled_final) if filled_final > 0 and cost_final > 0 else precio_orden
            log(f"[COMPRA PARCIAL] {symbol} | {filled_final} unidades a ${precio_fill:.2f} "
                f"(${filled_final * precio_fill:.2f})")
            return True, filled_final, _formatear_fee(order_final, "compra", filled_final * precio_fill), precio_fill

    return False, 0.0, "", 0.0


def ejecutar_venta(exchange, symbol, motivo, taker=False):
    """
    Vende TODO el balance del activo base en la testnet. Maneja el Min Notional
    (Riesgo #4): si el remanente vale < $5, Binance lo rechaza; lo reportamos
    como 'polvo' y liberamos la posicion para que el bot no quede trabado.
    taker=True  → MARKET sell (Stop Loss, emergencia).
    taker=False → LIMIT sell MAKER (TP, RSI, timeout, etc.), con fallback a market.
    Devuelve True si se considera resuelto (vendido o polvo asumido).
    """
    base = symbol.split('/')[0]

    # Balance desde WS cache, fallback a REST
    cantidad = 0.0
    if ws_manager and ws_manager.is_connected():
        cantidad = ws_manager.get_balance(base)
    if cantidad <= 0:
        ok_bal, balance = _con_reintentos(lambda: exchange.fetch_balance(), f"balance {symbol}")
        if ok_bal and balance:
            cantidad = float(balance.get('free', {}).get(base, 0.0) or 0.0)

    if cantidad <= 0:
        log(f"[VENTA-{motivo}] No hay saldo de {base} para vender. Posicion liberada.")
        return True

    # Precio desde WS cache, fallback a REST
    precio = 0.0
    if ws_manager and ws_manager.is_connected():
        precio = ws_manager.get_price(symbol)
    if precio <= 0:
        ok_tk, ticker = _con_reintentos(lambda: exchange.fetch_ticker(symbol), f"ticker {symbol}")
        precio = float(ticker['last']) if (ok_tk and ticker) else 0.0
    mn = MIN_NOTIONAL_USDT or MIN_NOTIONAL_FALLBACK
    if precio > 0 and cantidad * precio < mn:
        log(f"[VENTA-{motivo}] Remanente de {base} vale ~${cantidad * precio:.2f} (< ${mn:.2f}). "
            f"Es POLVO: Binance no deja venderlo. Se libera la posicion (revisar manualmente).")
        return True

    cantidad_fmt = float(exchange.amount_to_precision(symbol, cantidad))

    if taker:
        # Venta MARKET (taker) — inmediata
        ok, order = _con_reintentos(
            lambda: exchange.create_order(symbol, 'market', 'sell', cantidad_fmt),
            f"SELL {symbol}",
        )
        if ok and order is not None:
            fee_str = _formatear_fee(order, "venta", float(cantidad_fmt), precio) if order else ""
            log(f"[VENTA-{motivo}] {symbol}: {cantidad_fmt} {base} @ ${precio:.2f} = ${cantidad*precio:.2f} | {fee_str}")
            return True
    else:
        # Venta LIMIT MAKER — colocar orden al ask, esperar 30s, fallback a market
        precio_ask = precio
        ok_ob, ob = _con_reintentos(lambda: exchange.fetch_order_book(symbol, 5), f"orderbook {symbol}")
        if ok_ob and ob and ob.get('asks'):
            ask_price = float(ob['asks'][0][0])
            if ask_price > 0:
                precio_ask = ask_price
        try:
            precio_ask = float(exchange.price_to_precision(symbol, precio_ask))
        except Exception:
            pass

        ok, order = _con_reintentos(
            lambda: exchange.create_order(symbol, 'limit', 'sell', cantidad_fmt, precio_ask),
            f"LIMIT SELL {symbol}",
        )
        if ok and order is not None:
            order_id = order.get('id')
            fin = time.time() + 30
            while time.time() < fin:
                time.sleep(1)
                if ws_manager and ws_manager.is_connected():
                    updates = ws_manager.pop_order_updates()
                    for u in updates:
                        if u.get("order_id") == order_id:
                            if u["status"] == "FILLED":
                                filled = u["executed_qty"]
                                cost = u["cummulative_quote"]
                                precio_fill = (cost / filled) if filled > 0 and cost > 0 else precio_ask
                                fee_str = ""
                                log(f"[VENTA-{motivo} MAKER] {symbol}: {filled} {base} @ ${precio_fill:.2f} = ${cost:.2f}")
                                return True
                            if u["status"] in ("CANCELED", "EXPIRED"):
                                log(f"[VENTA-{motivo}] Orden LIMIT {u['status'].lower()}.")
                                break
                elapsed = time.time() - (fin - 30)
                if int(elapsed) % 5 == 0 and int(elapsed) > 0:
                    ok_chk, chk = _con_reintentos(
                        lambda: exchange.fetch_order(order_id, symbol),
                        f"check order {symbol}",
                    )
                    if ok_chk and chk:
                        status = chk.get('status', '')
                        filled = float(chk.get('filled', 0) or 0)
                        if status == 'closed':
                            cost = float(chk.get('cost', 0) or 0)
                            precio_fill = (cost / filled) if filled > 0 and cost > 0 else precio_ask
                            fee_str = _formatear_fee(chk, "venta", float(cantidad_fmt), precio_fill)
                            log(f"[VENTA-{motivo} MAKER] {symbol}: {filled} {base} @ ${precio_fill:.2f} = ${cost:.2f} | {fee_str}")
                            return True
                        if status in ('canceled', 'expired'):
                            break
            # Timeout o cancelacion: cancelar orden y reintentar como market
            _con_reintentos(lambda: exchange.cancel_order(order_id, symbol), f"cancel sell {symbol}")

    # Fallback a market si la maker fallo o timeout
    ok, order = _con_reintentos(
        lambda: exchange.create_order(symbol, 'market', 'sell', cantidad_fmt),
        f"SELL MARKET {symbol}",
    )
    if ok and order is not None:
        fee_str = _formatear_fee(order, "venta", float(cantidad_fmt), precio) if order else ""
        log(f"[VENTA-{motivo}] {symbol}: {cantidad_fmt} {base} @ ${precio:.2f} = ${cantidad*precio:.2f} | {fee_str} (fallback market)")
        return True

    # Si el rechazo fue por NOTIONAL, asumimos polvo para no trabar el bot
    if isinstance(order, ccxt.BaseError) and 'NOTIONAL' in str(order).upper():
        log(f"[VENTA-{motivo}] Rechazo por NOTIONAL. Se trata como polvo y se libera la posicion.")
        return True

    log(f"[VENTA-{motivo}] FALLO al vender {symbol}. Se REINTENTARA el proximo ciclo (posicion sigue abierta).")
    return False


# ==========================================================================
# GESTION AUTOMATICA DE BNB (Combustible de comisiones)
# ==========================================================================
def rebalancear_combustible_bnb(exchange, estado_bot):
    """
    Verifica que el valor de BNB sea el objetivo (dinamico o por % fijo).
    Si falta, compra usando USDT libre. Si no hay USDT suficiente, liquida
    una fraccion de BTC o ETH.
    """
    cap = calcular_capital_total(exchange, estado_bot)
    if cap["total_cuenta"] is None:
        return False

    total_real = cap["total_cuenta"]
    if total_real and total_real < 20:
        log(f"[COMBUSTIBLE BNB] Capital total ${total_real:.2f} < $20. "
            f"Compra BNB manualmente para cubrir fees. Se omite rebalanceo automatico.")
        return True
    objetivo_usdt = _calcular_objetivo_bnb()

    ok_bal, balance = _con_reintentos(lambda: exchange.fetch_balance(), "rebalanceo bnb")
    if not ok_bal or not balance:
        return False

    bnb_libre = float(balance.get('free', {}).get('BNB', 0.0) or 0.0)
    usdt_libre = float(balance.get('free', {}).get('USDT', 0.0) or 0.0)

    # No consumir mas del 50% del USDT libre en BNB
    if usdt_libre and objetivo_usdt > usdt_libre * 0.5:
        objetivo_usdt = usdt_libre * 0.5
        objetivo_usdt = max(MIN_TRADE_USDT, objetivo_usdt)
        log(f"[COMBUSTIBLE BNB] Objetivo reducido a ${objetivo_usdt:.2f} (max 50% del USDT libre).")

    ok_tk, tk_bnb = _con_reintentos(lambda: exchange.fetch_ticker('BNB/USDT'), "ticker bnb")
    if not ok_tk or not tk_bnb:
        return False

    precio_bnb = float(tk_bnb.get('last', 0) or 0)
    if precio_bnb <= 0:
        return False

    valor_actual_usdt = bnb_libre * precio_bnb

    # Margen de tolerancia del 15% para evitar rebalanceos por micro-movimientos
    limite_inferior = objetivo_usdt * 0.85
    limite_superior = objetivo_usdt * 1.15

    if limite_inferior <= valor_actual_usdt <= limite_superior:
        return True  # BNB esta en el rango deseado

    if valor_actual_usdt < limite_inferior:
        faltante_usdt = objetivo_usdt - valor_actual_usdt
        mn = MIN_NOTIONAL_FALLBACK
        monto_compra_usdt = max(faltante_usdt, mn)

        if usdt_libre >= monto_compra_usdt:
            log(f"[COMBUSTIBLE BNB] Balance bajo (${valor_actual_usdt:.2f} de ${objetivo_usdt:.2f}). "
                f"Comprando ${monto_compra_usdt:.2f} con USDT libre...")
            params = {'quoteOrderQty': round(monto_compra_usdt, 2)}
            ok_bnb, order_bnb = _con_reintentos(
                lambda: exchange.create_order('BNB/USDT', 'market', 'buy', None, None, params),
                "BUY BNB",
            )
            if ok_bnb and order_bnb:
                fee_str = _formatear_fee(order_bnb, "compra", monto_compra_usdt) if order_bnb else ""
                log(f"[COMBUSTIBLE BNB] Compra completada. | {fee_str}")
            return True
        else:
            log(f"[ALERTA BNB] Faltan ${faltante_usdt:.2f} (objetivo ${objetivo_usdt:.2f}), "
                f"pero solo hay ${usdt_libre:.2f} USDT libre. Liquidando posiciones activas de rescate...")
            
            # Vender SOLO la fraccion necesaria de BTC/ETH (no la posicion entera)
            monto_necesario = faltante_usdt + 2.0  # un extra para cubrir fees
            for sym in SYMBOL_LIST:
                pos = estado_bot["posiciones"].get(sym)
                if not pos:
                    continue
                base = sym.split('/')[0]
                cantidad_pos = pos.get("cantidad", 0)
                precio_compra = pos.get("precio_compra", 0)
                if cantidad_pos <= 0 or precio_compra <= 0:
                    continue
                valor_pos = cantidad_pos * precio_compra
                if valor_pos <= 0:
                    continue
                # Vender hasta 50% de la posicion, lo justo para cubrir el faltante
                fraccion = min(monto_necesario / valor_pos, 0.5)
                cantidad_vender = cantidad_pos * fraccion
                ok_tk, tk = _con_reintentos(lambda: exchange.fetch_ticker(sym), f"ticker {sym}")
                if not ok_tk or not tk:
                    continue
                precio_actual = float(tk.get('last', 0) or 0)
                if precio_actual <= 0:
                    continue
                # No vender si la posicion esta en perdida
                if precio_actual < precio_compra:
                    log(f"[REBALANCEO] {sym}: saltando venta para BNB, posicion en perdida "
                        f"(precio ${precio_actual:.2f} < compra ${precio_compra:.2f}).")
                    continue
                cantidad_vender_fmt = float(exchange.amount_to_precision(sym, cantidad_vender))
                if cantidad_vender_fmt <= 0:
                    continue
                ok_venta, order = _con_reintentos(
                    lambda: exchange.create_order(sym, 'market', 'sell', cantidad_vender_fmt),
                    f"SELL {sym} para BNB",
                )
                if ok_venta and order is not None:
                    vendido = float(order.get('filled', cantidad_vender) or cantidad_vender)
                    log(f"[REBALANCEO] Vendida fraccion de {sym}: {vendido:.4f} {base} (~${vendido*precio_actual:.2f}) para BNB.")
                    nueva_cant = round(cantidad_pos - vendido, 8)
                    if nueva_cant <= 0:
                        estado_bot["posiciones"][sym] = None
                    else:
                        estado_bot["posiciones"][sym]["cantidad"] = nueva_cant
                    guardar_estado(estado_bot)
                    return False
                else:
                    log(f"[REBALANCEO] No se pudo vender fraccion de {sym}.")

            log("[ALERTA CRITICA] No se pudo fondear BNB desde posiciones.")
            return False

    return True


# ==========================================================================
# NUCLEO LOGICO DE TRADING (CICLO RECURRENTE)
# ==========================================================================
def ejecutar_ciclo_trading(exchange, estado_bot, cap=None):
    ahora = datetime.datetime.now()

    # --- Reset diario de trades ejecutados ---
    ultima_limpieza = _parse_dt(estado_bot["ultima_limpieza_diaria"]) or ahora
    if ultima_limpieza.date() < ahora.date():
        estado_bot["trades_executed_today"] = 0
        estado_bot["drawdown_max_del_dia"] = 0.0
        estado_bot["ultima_limpieza_diaria"] = ahora.isoformat()
        cap_ref = cap if cap is not None else calcular_capital_total(exchange, estado_bot)
        total = cap_ref.get("total_cuenta") if cap_ref else None
        if DRAWDOWN_DIARIO_MAX_PCT > 0 and total is not None:
            estado_bot["capital_inicio_dia"] = total
            log(f"[DRAWDOWN] Capital de referencia diario: ${total:.2f}")
        guardar_estado(estado_bot)
        log("[SISTEMA] Reset diario: contador de trades a 0.")

    # --- Recopilar indicadores de todos los simbolos (una sola vez) ---
    datos = {}
    for symbol in SYMBOL_LIST:
        rsi, atr_pct, precio_actual, sma200, ema50, metricas = obtener_indicadores_tecnicos(exchange, symbol)
        if rsi is not None:
            datos[symbol] = {
                "rsi": rsi, "precio": precio_actual,
                "sma200": sma200, "ema50": ema50, "metricas": metricas,
            }
            if metricas:
                ULTIMAS_METRICAS[symbol] = metricas
            meta = ULTIMAS_METRICAS.setdefault(symbol, {})
            meta["rsi"] = rsi
            meta["sma200"] = sma200
            meta["ema50"] = ema50
            meta["precio"] = precio_actual
        else:
            log(f"[DEBUG] {symbol}: indicadores NO disponibles (rsi=None). "
                f"WS connected={ws_manager.is_connected() if ws_manager else False}")

    if not datos:
        log(f"[DEBUG] Ningun simbolo tiene datos. Posiciones abiertas: "
            f"{[(s, p is not None) for s, p in estado_bot['posiciones'].items()]}")
    if ws_manager:
        ws_connected = ws_manager.is_connected()
        if ws_connected != getattr(ejecutar_ciclo_trading, "_ws_prev_connected", None):
            estado = "conectado" if ws_connected else "desconectado"
            log(f"[WS] Estado: {estado}.")
            ejecutar_ciclo_trading._ws_prev_connected = ws_connected

    # =================================================================
    # PASO 1: VENTAS (evaluar posiciones abiertas, prioridad absoluta)
    # =================================================================
    for symbol in SYMBOL_LIST:
        posicion = estado_bot["posiciones"][symbol]
        if posicion is None:
            continue
        d = datos.get(symbol)
        if d is None:
            continue
        precio_actual = d["precio"]
        precio_compra = posicion["precio_compra"]
        stop_loss = posicion["stop_loss"]
        fecha_entrada = _parse_dt(posicion["fecha_entrada"])
        t_act = posicion.get("trailing_activa_pct", TRAILING_ACTIVA_MIN)
        t_dist = posicion.get("trailing_distancia_pct", TRAILING_DISTANCIA_MIN)
        cd_hs = posicion.get("cooldown_horas", COOLDOWN_MIN_HORAS)

        take_profit = posicion.get("take_profit", precio_compra * (1 + TP_PCT))
        tp_pct = (take_profit / precio_compra - 1) if precio_compra > 0 else TP_PCT

        # Momento alcista: si velas suben, no vender en TP (esperar)
        momento_alcista = ULTIMAS_METRICAS.get(symbol, {}).get("momento_alcista", False)

        # Minutos desde entrada
        min_desde_entrada = (ahora - fecha_entrada).total_seconds() / 60 if fecha_entrada else 0

        # P&L actual (estimado)
        pnl_actual = (precio_actual / precio_compra - 1) if precio_compra > 0 else 0

        # 1. Stop-Loss (PRIORIDAD ABSOLUTA)
        if precio_actual <= stop_loss:
            log(f"[STOP LOSS] {symbol}: precio ${precio_actual:.2f} <= SL ${stop_loss:.2f}. Vendiendo.")
            if ejecutar_venta(exchange, symbol, "STOP_LOSS", taker=True):
                pnl = posicion["cantidad"] * stop_loss * (1 - BINANCE_FEE_RATE) - posicion["monto_invertido"]
                estado_bot["balance_trades"] = round(estado_bot.get("balance_trades", 0.0) + pnl, 2)
                estado_bot["posiciones"][symbol] = None
                estado_bot["ultima_maniobra_time"][symbol] = ahora.isoformat()
                estado_bot["cooldown_until"][symbol] = (ahora + datetime.timedelta(hours=cd_hs)).isoformat()
                guardar_estado(estado_bot)
                log(f"[BALANCE] {symbol}: PnL {pnl:+.2f}  balance ${estado_bot['balance_trades']:.2f}")
            continue

        # Trailing Stop adaptativo
        nuevo_piso = precio_actual * (1 - t_dist)
        if precio_actual > precio_compra * (1 + t_act) and nuevo_piso > stop_loss:
            estado_bot["posiciones"][symbol]["stop_loss"] = nuevo_piso
            guardar_estado(estado_bot)
            stop_loss = nuevo_piso
            log(f"[TRAILING] {symbol}: SL elevado a ${stop_loss:.2f} (precio ${precio_actual:.2f}).")

        # 2. Take-Profit fijo
        if precio_actual >= take_profit:
            if momento_alcista:
                log(f"[MOMENTO] {symbol}: precio ${precio_actual:.2f} >= TP ${take_profit:.2f} "
                    f"pero con momento alcista. Difiriendo venta.")
            else:
                log(f"[TAKE PROFIT] {symbol}: precio ${precio_actual:.2f} >= TP ${take_profit:.2f} "
                    f"(TP={tp_pct*100:.2f}%). Vendiendo.")
                if ejecutar_venta(exchange, symbol, "TAKE_PROFIT"):
                    pnl = posicion["cantidad"] * take_profit * (1 - BINANCE_FEE_RATE) - posicion["monto_invertido"]
                    estado_bot["balance_trades"] = round(estado_bot.get("balance_trades", 0.0) + pnl, 2)
                    estado_bot["posiciones"][symbol] = None
                    estado_bot["cooldown_until"][symbol] = None
                    guardar_estado(estado_bot)
                    log(f"[BALANCE] {symbol}: PnL {pnl:+.2f}  balance ${estado_bot['balance_trades']:.2f}")
                continue

        # 3. RSI sobrecompra
        rsi_actual = d.get("rsi")
        rsi_umbral_venta = RSI_SOBRECOMPRA.get(symbol, 70)
        if rsi_actual is not None and rsi_actual > rsi_umbral_venta and pnl_actual > 0.0035:
            log(f"[RSI SOBRECOMPRA] {symbol}: RSI={rsi_actual:.1f} > {rsi_umbral_venta:.0f}, ganancia {pnl_actual*100:.2f}%. Vendiendo.")
            if ejecutar_venta(exchange, symbol, "RSI_SOBRECOMPRA"):
                pnl = posicion["cantidad"] * precio_actual * (1 - BINANCE_FEE_RATE) - posicion["monto_invertido"]
                estado_bot["balance_trades"] = round(estado_bot.get("balance_trades", 0.0) + pnl, 2)
                estado_bot["posiciones"][symbol] = None
                estado_bot["ultima_maniobra_time"][symbol] = ahora.isoformat()
                guardar_estado(estado_bot)
                log(f"[BALANCE] {symbol}: PnL {pnl:+.2f}  balance ${estado_bot['balance_trades']:.2f}")
            continue

        # 4. Timeout 60min: solo si ganancia >= 0.35% y < TP (salvo momento alcista)
        if min_desde_entrada >= 60 and pnl_actual > 0.0035 and pnl_actual < tp_pct:
            if momento_alcista:
                log(f"[MOMENTO] {symbol}: Timeout {min_desde_entrada:.0f}min con ganancia "
                    f"{pnl_actual*100:.2f}% pero con momento alcista. Difiriendo venta.")
            else:
                log(f"[TIMEOUT 60min] {symbol}: {min_desde_entrada:.0f}min, ganancia {pnl_actual*100:.2f}% "
                    f"(min 0.35%) < TP {tp_pct*100:.2f}%. Vendiendo (estable).")
                if ejecutar_venta(exchange, symbol, "TIMEOUT_60MIN"):
                    pnl = posicion["cantidad"] * precio_actual * (1 - BINANCE_FEE_RATE) - posicion["monto_invertido"]
                    estado_bot["balance_trades"] = round(estado_bot.get("balance_trades", 0.0) + pnl, 2)
                    estado_bot["posiciones"][symbol] = None
                    estado_bot["ultima_maniobra_time"][symbol] = ahora.isoformat()
                    guardar_estado(estado_bot)
                    log(f"[BALANCE] {symbol}: PnL {pnl:+.2f}  balance ${estado_bot['balance_trades']:.2f}")
            continue

        # 5. SMA200 (anticuchillo)
        sma200 = d.get("sma200")
        if sma200 and precio_actual < sma200 * (1 - SMA_DISTANCIA_MAX_PCT):
            log(f"[SMA200] {symbol}: precio ${precio_actual:.2f} < SMA200 ${sma200:.2f}. "
                f"Vendiendo (anticuchillo).")
            if ejecutar_venta(exchange, symbol, "SMA200"):
                pnl = posicion["cantidad"] * precio_actual * (1 - BINANCE_FEE_RATE) - posicion["monto_invertido"]
                estado_bot["balance_trades"] = round(estado_bot.get("balance_trades", 0.0) + pnl, 2)
                estado_bot["posiciones"][symbol] = None
                estado_bot["ultima_maniobra_time"][symbol] = ahora.isoformat()
                estado_bot["cooldown_until"][symbol] = (ahora + datetime.timedelta(hours=cd_hs)).isoformat()
                guardar_estado(estado_bot)
                log(f"[BALANCE] {symbol}: PnL {pnl:+.2f}  balance ${estado_bot['balance_trades']:.2f}")
            continue



    # Drawdown diario: calcular una sola vez por ciclo
    drawdown_bloqueado = False
    if DRAWDOWN_DIARIO_MAX_PCT > 0 and estado_bot.get("capital_inicio_dia") is not None and estado_bot["capital_inicio_dia"] > 0:
        cap_dd = cap if cap is not None else calcular_capital_total(exchange, estado_bot)
        if cap_dd["total_cuenta"] is not None:
            perdida = (estado_bot["capital_inicio_dia"] - cap_dd["total_cuenta"]) / estado_bot["capital_inicio_dia"]
            estado_bot["drawdown_max_del_dia"] = round(max(perdida, 0), 4)
            if perdida > DRAWDOWN_DIARIO_MAX_PCT:
                log(f"[DRAWDOWN] Perdida diaria {perdida*100:.1f}% > {DRAWDOWN_DIARIO_MAX_PCT*100:.0f}%. "
                    f"Compras bloqueadas por hoy.")
                drawdown_bloqueado = True

    candidatos = []
    for symbol in SYMBOL_LIST:
        d = datos.get(symbol)
        if d is None:
            continue
        if estado_bot["trades_executed_today"] >= MAX_DAILY_TRADES:
            continue
        if estado_bot["posiciones"].get(symbol) is not None:
            continue

        precio_actual = d["precio"]
        sma200 = d.get("sma200")
        ema50 = d.get("ema50")
        rsi = d["rsi"]
        metricas = d["metricas"]

        ultima_m = _parse_dt(estado_bot["ultima_maniobra_time"][symbol])
        if ultima_m and (ahora - ultima_m).total_seconds() / 3600 < MIN_INTERVALO_MANIOBRAS_HORAS:
            continue

        cd = _parse_dt(estado_bot["cooldown_until"][symbol])
        if cd:
            if ahora < cd:
                continue
            estado_bot["cooldown_until"][symbol] = None

        # Filtro Anticuchillo: SMA200 si hay datos, si no EMA50 como fallback
        # Permite comprar en retrocesos leves hasta SMA_DISTANCIA_MAX_PCT por debajo de la media
        ma = sma200 if sma200 is not None else ema50
        ma_nombre = "SMA200" if sma200 is not None else "EMA50"
        if ma is not None and SMA_FILTR0_HABILITADO:
            distancia_ma = (ma - precio_actual) / ma
            if distancia_ma > SMA_DISTANCIA_MAX_PCT:
                log(f"[TENDENCIA BAJISTA] {symbol}: precio ${precio_actual:.2f} esta "
                    f"{distancia_ma*100:.1f}% por debajo de {ma_nombre} (${ma:.2f}). Compra BLOQUEADA.")
                continue


        if drawdown_bloqueado:
            continue

        if not metricas:
            sl_pct = 0.02; t_act = TRAILING_ACTIVA_MIN; t_dist = TRAILING_DISTANCIA_MIN; cd_hs = COOLDOWN_MIN_HORAS
            tp_pct_cand = TP_PCT
        else:
            sl_pct = metricas["sl"]; t_act = metricas["t_act"]; t_dist = metricas["t_dist"]; cd_hs = metricas["cd"]
            tp_pct_cand = metricas.get("tp", TP_PCT)

        rsi_ent = RSI_ENTRADA.get(symbol, 30)
        if rsi < rsi_ent:
            score = (tp_pct_cand / sl_pct) if sl_pct > 0 else 0
            candidatos.append({
                "symbol": symbol, "score": score,
                "sl_pct": sl_pct, "rsi_ent": rsi_ent, "rsi": rsi,
                "t_act": t_act, "t_dist": t_dist, "cd_hs": cd_hs,
                "tp_pct": tp_pct_cand,
                "precio": precio_actual, "metricas": metricas,
            })

    if not candidatos:
        return

    mejor = max(candidatos, key=lambda c: c["score"])
    symbol = mejor["symbol"]
    sl_pct = mejor["sl_pct"]; rsi_ent = mejor["rsi_ent"]
    rsi = mejor["rsi"]
    t_act = mejor["t_act"]; t_dist = mejor["t_dist"]; cd_hs = mejor["cd_hs"]
    tp_pct = mejor.get("tp_pct", TP_PCT)
    precio_actual = mejor["precio"]
    metricas = mejor["metricas"]

    monto_usdt, saldo = calcular_monto_orden(exchange, symbol, tp_pct)
    if monto_usdt is None:
        if saldo is None:
            log(f"[COMPRA] {symbol}: no se pudo leer saldo USDT de Binance. Se omite.")
        else:
            log(f"[COMPRA] {symbol}: saldo insuficiente (${saldo:.2f}). Se omite.")
        return

    vol_log = f"volatilidad 7d={metricas['vol']*100:.2f}%" if metricas else "volatilidad=s/d"
    log(f"[SENAL COMPRA] {symbol} | RSI={rsi:.1f} (umbral {rsi_ent:.0f}) | "
        f"{vol_log} | "
        f"TP={tp_pct*100:.2f}% | "
        f"precio=${precio_actual:.2f} | invirtiendo ${monto_usdt:.2f}")

    ganancia_abs = monto_usdt * tp_pct
    if PROFIT_FLOOR_USDT is not None and ganancia_abs < PROFIT_FLOOR_USDT:
        min_needed = PROFIT_FLOOR_USDT / tp_pct
        log(f"[CALIDAD] {symbol}: ganancia esperada ${ganancia_abs:.4f} "
            f"< piso ${PROFIT_FLOOR_USDT:.4f}. "
            f"Se necesitarian ${min_needed:.2f} de inversion. Abortando.")
        return

    ok, cantidad, fee_str, precio_fill = ejecutar_compra(exchange, symbol, precio_actual, monto_usdt)
    if not ok:
        log(f"[COMPRA] {symbol}: no se concreto. Actualizando gating.")
        estado_bot["ultima_maniobra_time"][symbol] = ahora.isoformat()
        guardar_estado(estado_bot)
        return

    compra_precio = precio_fill if precio_fill > 0 else precio_actual
    take_profit_real = compra_precio * (1 + tp_pct)
    costo_real = round(cantidad * compra_precio, 2)
    estado_bot["posiciones"][symbol] = {
        "precio_compra": compra_precio,
        "cantidad": cantidad,
        "monto_invertido": costo_real,
        "stop_loss": compra_precio * (1 - sl_pct),
        "take_profit": take_profit_real,
        "fecha_entrada": ahora.isoformat(),
        "trailing_activa_pct": t_act,
        "trailing_distancia_pct": t_dist,
        "cooldown_horas": cd_hs,
    }
    estado_bot["ultima_maniobra_time"][symbol] = ahora.isoformat()
    estado_bot["trades_executed_today"] += 1
    guardar_estado(estado_bot)
    log(f"[COMPRA OK] {symbol} | {cantidad} unidades (${monto_usdt:.2f}) | "
        f"SL=${estado_bot['posiciones'][symbol]['stop_loss']:.2f} "
        f"TP=${estado_bot['posiciones'][symbol]['take_profit']:.2f} | {fee_str} | "
        f"balance ${estado_bot.get('balance_trades', 0.0):+.2f}")


# ==========================================================================
# DASHBOARD (limpia pantalla y redibuja todo cada ciclo)
# ==========================================================================
def _limpiar_pantalla():
    os.system('cls' if sys.platform == 'win32' else 'clear')


def dibujar_dashboard(exchange, estado_bot, cap=None):
    """Limpia la pantalla y redibuja el estado completo del bot."""
    ahora = datetime.datetime.now()
    cyan  = "96"; amar = "93"; verde = "92"; rojo = "91"; gris = "90"
    W     = 78

    def b():
        print(_c("  +" + "-" * W + "+", cyan), flush=True)

    def fila_col(texto, col="0"):
        pad = max(0, W - len(texto) - 1)
        print(_c("  |", cyan) + " " + _c(texto, col) + " " * pad + _c("|", cyan), flush=True)

    def sep(titulo):
        t = f" {titulo} "
        print("  " + _c(t + "-" * (W - len(t)), amar), flush=True)

    _limpiar_pantalla()

    if cap is None:
        cap = calcular_capital_total(exchange, estado_bot)

    # ── BANNER ──────────────────────────────────────────────────────────────
    for l in [
        " ____  _ _ _ _   _ ____   ___ _____   ____   ___ _____",
        "| __ )| | | | \\ | |  _ \\ / _ \\_   _| | __ ) / _ \\_   _|",
        "|  _ \\| | | |  \\| | | | | | | || |   |  _ \\| | | || |",
        "| |_) | | | | |\\  | |_| | |_| || |   | |_) | |_| || |",
        "|____/|_|_|_|_| \\_|____/ \\___/ |_|   |____/ \\___/ |_|",
    ]:
        print(_c("  " + l, cyan), flush=True)
    print("  " + _c("BOT TRADING v4.6", amar) +
          "  " + _c(ahora.strftime("%Y-%m-%d %H:%M:%S"), gris) +
          "  " + _c(f"[{os.getenv('ENTORNO', '?').upper()}]", verde) +
          "  " + _c("[24/7]", verde), flush=True)
    print("", flush=True)

    # ── CAPITAL ─────────────────────────────────────────────────────────────
    if cap["total_cuenta"] is not None:
        tc       = f"${cap['total_cuenta']:,.2f}"
        dsp      = f"${cap['disponible']:,.2f}"
        bal      = estado_bot.get("balance_trades", 0.0)
        bal_str  = f"${bal:+.2f}"
        bal_col  = verde if bal > 0 else (rojo if bal < 0 else gris)
        cap_col  = (f"Cuenta {_c(tc, amar)}  "
                    f"Disponible {_c(dsp, verde)}  "
                    f"Balance {_c(bal_str, bal_col)}  "
                    f"Inversion {_c(f'${MIN_TRADE_USDT:.1f}', gris)}")
        print("  " + cap_col, flush=True)
    else:
        print("  " + _c("Capital: no se pudo leer desde Binance.", rojo), flush=True)
    print("", flush=True)
    b()

    # ── ACTIVOS LIBRES ──────────────────────────────────────────────────────
    free = cap.get("free", {})
    if free:
        usdt = float(free.get('USDT', 0) or 0)
        btc  = float(free.get('BTC',  0) or 0)
        eth  = float(free.get('ETH',  0) or 0)
        bnb  = float(free.get('BNB',  0) or 0)
        bnb_pct_str = ""
        if cap["total_cuenta"] and cap["total_cuenta"] > 0 and cap["bnb_precio"]:
            pct = (bnb * cap["bnb_precio"] / cap["total_cuenta"]) * 100
            obj_bnb = _calcular_objetivo_bnb()
            target_pct_show = (obj_bnb / cap["total_cuenta"]) * 100
            col_pct = verde if pct >= (target_pct_show * 0.8) else rojo
            bnb_pct_str = f" ({_c(f'{pct:.1f}%', col_pct)})"
        vis = f"Activos: USDT ${usdt:,.2f}   BTC {btc:.5f}   ETH {eth:.4f}   BNB {bnb:.3f}"
        col = (f"Activos: USDT {_c(f'${usdt:,.2f}', verde)}   "
               f"BTC {_c(f'{btc:.5f}', amar)}   "
               f"ETH {_c(f'{eth:.4f}', amar)}   "
               f"BNB {_c(f'{bnb:.3f}', gris)}{bnb_pct_str}")
        pad = max(0, W - len(vis) - 1)
        print(_c("  |", cyan) + " " + col + " " * pad + _c("|", cyan), flush=True)
    else:
        fila_col("Activos: sin datos", rojo)

    sep("POSICIONES")

    # ── POSICIONES ──────────────────────────────────────────────────────────
    for symbol in SYMBOL_LIST:
        pos     = estado_bot["posiciones"][symbol]
        base    = symbol.split('/')[0]
        rsi_actual = ULTIMAS_METRICAS.get(symbol, {}).get("rsi")
        if pos:
            fe     = _parse_dt(pos["fecha_entrada"])
            horas  = (ahora - fe).total_seconds() / 3600 if fe else 0.0
            monto  = pos.get('monto_invertido', 0)
            pc     = pos['precio_compra']
            sl     = pos['stop_loss']
            tp     = pos['take_profit']
            precio_actual = ULTIMAS_METRICAS.get(symbol, {}).get("precio", 0)
            pnl    = ((precio_actual / pc - 1) * 100) if pc > 0 and precio_actual else 0
            pnl_col = verde if pnl >= 0 else rojo
            vis = (f"{base} EN POSICION  entrada ${pc:.2f}  "
                   f"actual ${precio_actual:.2f} ({pnl:+.2f}%)  "
                   f"Stop Loss ${sl:.2f}  Take Profit ${tp:.2f}  "
                   f"${monto:.0f} invertido  {horas:.1f} horas")
            col = (f"{_c(base + ' EN POSICION', verde)}  "
                   f"entrada ${pc:.2f}  "
                   f"actual {_c(f'${precio_actual:.2f} ({pnl:+.2f}%)', pnl_col)}  "
                   f"Stop Loss {_c(f'${sl:.2f}', rojo)}  "
                   f"Take Profit {_c(f'${tp:.2f}', verde)}  "
                   f"${monto:.0f} invertido  {horas:.1f} horas")
            pad = max(0, W - len(vis) - 1)
            print(_c("  |", cyan) + " " + col + " " * pad + _c("|", cyan), flush=True)
        else:
            rsi_entrada = RSI_ENTRADA.get(symbol, 30)
            rsi_sobrecompra = RSI_SOBRECOMPRA.get(symbol, 70)
            rsi_txt = f"RSI {rsi_actual:.0f}" if rsi_actual is not None else "RSI sin datos"
            rsi_col = verde if (rsi_actual is not None and rsi_actual <= rsi_entrada) else (
                       rojo if (rsi_actual is not None and rsi_actual >= rsi_sobrecompra) else amar)
            fila_col(f"{base}  LIQUIDO  {_c(rsi_txt, rsi_col)}  "
                     f"(entrada < {rsi_entrada}  sobrecompra > {rsi_sobrecompra})", gris)

    sep("PROTECCIONES")

    # ── PROTECCIONES GLOBALES ───────────────────────────────────────────────
    total_hoy = estado_bot['trades_executed_today']
    dd_ref = estado_bot.get("capital_inicio_dia")
    dd_act = cap.get("total_cuenta")
    dd_pct = ((dd_ref - dd_act) / dd_ref) if (dd_ref and dd_act and dd_ref > 0) else 0
    dd_ok = dd_pct <= DRAWDOWN_DIARIO_MAX_PCT
    dd_txt = f"Drawdown {dd_pct*100:.1f}%" if dd_ref else "Drawdown sin referencia"
    dd_col = verde if dd_ok else rojo
    g_vis = f"Trades hoy {total_hoy} / {MAX_DAILY_TRADES} maximo  |  {dd_txt}"
    g_col = (f"Trades hoy {_c(str(total_hoy), amar)} / {_c(str(MAX_DAILY_TRADES), gris)} maximo  |  "
             f"{_c(dd_txt, dd_col)}")
    pad = max(0, W - len(g_vis) - 1)
    print(_c("  |", cyan) + " " + g_col + " " * pad + _c("|", cyan), flush=True)

    # ── PROTECCIONES POR MONEDA ─────────────────────────────────────────────
    for symbol in SYMBOL_LIST:
        base = symbol.split('/')[0]
        cd   = _parse_dt(estado_bot["cooldown_until"][symbol])
        um   = _parse_dt(estado_bot["ultima_maniobra_time"][symbol])
        hm   = (ahora - um).total_seconds() / 3600 if um else 999
        cd_vis = (f"enfriamiento {int((cd-ahora).total_seconds()/60)} min" if cd and ahora < cd else "enfriamiento libre")
        cd_col = _c(cd_vis, rojo) if cd and ahora < cd else _c(cd_vis, verde)
        gm = int((MIN_INTERVALO_MANIOBRAS_HORAS - hm) * 60)
        gat_vis = f"bloqueo {gm} min" if hm < MIN_INTERVALO_MANIOBRAS_HORAS else "bloqueo libre"
        gat_col = _c(gat_vis, amar) if hm < MIN_INTERVALO_MANIOBRAS_HORAS else _c(gat_vis, verde)
        vol = ULTIMAS_METRICAS.get(symbol, {}).get("vol")
        vol_str = f"volatilidad 7d {vol*100:.1f}%" if vol else ""
        meta = ULTIMAS_METRICAS.get(symbol, {})
        precio = meta.get("precio", 0)
        sma_val = meta.get("sma200")
        ema_val = meta.get("ema50")
        if sma_val:
            sma_ok = precio >= sma_val * (1 - SMA_DISTANCIA_MAX_PCT)
            sma_txt = "SMA200 bueno" if sma_ok else f"SMA200 {(precio/sma_val-1)*100:.1f}%"
            sma_col = verde if sma_ok else rojo
        else:
            sma_txt = "SMA200 sin datos"; sma_col = gris
        if ema_val:
            ema_ok = precio >= ema_val * (1 - SMA_DISTANCIA_MAX_PCT)
            ema_txt = "EMA50 bueno" if ema_ok else f"EMA50 {(precio/ema_val-1)*100:.1f}%"
            ema_col = verde if ema_ok else rojo
        else:
            ema_txt = "EMA50 sin datos"; ema_col = gris
        lin = f"{base}:  {cd_vis}  {gat_vis}"
        if vol:
            lin += f"  {vol_str}"
        lin += f"  {sma_txt}  {ema_txt}"
        pad = max(0, W - len(lin) - 1)
        print(_c("  |", cyan) + " " + _c(base, amar) + ":  " +
              cd_col + "  " + gat_col +
              (f"  {_c(vol_str, gris)}" if vol else "") +
              f"  {_c(sma_txt, sma_col)}  {_c(ema_txt, ema_col)}" +
              " " * pad + _c("|", cyan), flush=True)

    sep("EVENTOS")

    # ── EVENTOS ─────────────────────────────────────────────────────────────
    logs = _LOG_BUFFER[-LOG_BUFFER_MAX:] if _LOG_BUFFER else ["Sin eventos aun..."]
    for linea in logs:
        visible = linea[:W - 2]
        pad = max(0, W - len(visible))
        print(_c("  |", cyan) + " " + _c(visible, gris) + " " * pad + _c("|", cyan), flush=True)

    b()
    print("", flush=True)





# Colores ANSI con fallback usados por el dashboard en vivo.
_USAR_COLOR = True
try:
    if sys.platform == "win32":
        os.system("")  # activa el procesamiento de secuencias ANSI en cmd/PowerShell
except Exception:
    _USAR_COLOR = False


def _c(texto, codigo):
    """Envuelve texto en color ANSI si esta habilitado."""
    if not _USAR_COLOR:
        return texto
    return f"\033[{codigo}m{texto}\033[0m"


# ==========================================================================
# BUCLE PRINCIPAL DE EJECUCION
# ==========================================================================
def _activar_modo_vigilia():
    """
    Impide que Windows suspenda la PC o apague la pantalla mientras el bot
    corre. Usa SetThreadExecutionState de la API de Windows via ctypes.
    Se llama una vez al arrancar; Windows lo revierte automaticamente al
    cerrar el proceso (Ctrl+C / cierre de ventana).
    """
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        # ES_CONTINUOUS        = 0x80000000 -> mantener el estado mientras el proceso viva
        # ES_SYSTEM_REQUIRED   = 0x00000001 -> no suspender el sistema
        # ES_DISPLAY_REQUIRED  = 0x00000002 -> no apagar la pantalla
        ES_CONTINUOUS      = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ES_DISPLAY_REQUIRED = 0x00000002
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
        )
        log("[SISTEMA] Modo vigilia activo: suspension y protector de pantalla deshabilitados.")
    except Exception as e:
        log(f"[SISTEMA] No se pudo activar modo vigilia: {e}")


def _cargar_env():
    """
    Carga el archivo .env desde conf/ segun ENVIRONMENT_SELECTED definido en bot.properties.
    Si no esta definido en bot.properties, usa desa.env como default.
    """
    env_selected = "desa.env"
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf", "bot.properties")
    if os.path.exists(cfg_path):
        with open(cfg_path, 'r', encoding='utf-8') as f:
            for linea in f:
                linea = linea.strip()
                if not linea or linea.startswith('#'):
                    continue
                if '=' not in linea:
                    continue
                clave, _, valor = linea.partition('=')
                if clave.strip() == "ENVIRONMENT_SELECTED":
                    env_selected = valor.split('#')[0].strip()
                    break
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf", env_selected)
    if not os.path.exists(env_path):
        print(f"[ERROR] No se encuentra {env_path}.")
        print(f"Verifica ENVIRONMENT_SELECTED en conf/bot.properties.")
        sys.exit(1)
    load_dotenv(env_path)
    return env_path


def main():
    global LOG_ARCHIVO_PATH, PATH_ESTADO, PATH_ESTADO_TMP, PATH_ESTADO_BAK, ws_manager
    global _ULTIMA_ACT_REFRESH_NOTIONAL

    _cargar_env()

    # Cargar config unificada
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf", "bot.properties")
    if os.path.exists(cfg_path):
        _init_config(cfg_path)
    else:
        _init_config()
        log(f"[CONFIG] {cfg_path} no encontrado, usando defaults por defecto.")

    # Paths de estado unificados
    PATH_ESTADO     = _PATH_ESTADO
    PATH_ESTADO_TMP = _PATH_ESTADO_TMP
    PATH_ESTADO_BAK = _PATH_ESTADO_BAK

    # Log con fecha rotatoria: logs/bot_trading_2026-06-15.log
    log_base, log_ext = os.path.splitext(LOG_ARCHIVO_PATH)
    LOG_ARCHIVO_PATH = f"{log_base}_{datetime.datetime.now().strftime('%Y-%m-%d')}{log_ext}"
    os.makedirs("logs", exist_ok=True)

    _activar_modo_vigilia()

    log(f"[SISTEMA] Entorno: {os.getenv('ENTORNO', '?').upper()}")
    log(f"[SISTEMA] Config: {cfg_path}")
    log(f"[SISTEMA] Archivo de log: {LOG_ARCHIVO_PATH}")
    log(f"[SISTEMA] Archivo de estado: {PATH_ESTADO}")

    estado_bot = cargar_estado()

    # Conexion con reintento inicial: no arrancar el bucle si nunca conecta.
    bot_exchange = None
    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            bot_exchange = inicializar_exchange()
            break
        except SystemExit:
            raise
        except Exception as e:
            log(f"[CONEXION] Fallo inicial ({intento}/{MAX_REINTENTOS}): {e}")
            time.sleep(BACKOFF_SEGUNDOS * intento)
    if bot_exchange is None:
        log("[ERROR FATAL] No se pudo conectar a Binance tras varios intentos. Abortando.")
        raise SystemExit(1)

    # Verificacion de saldo inicial para poder operar
    saldo_inicial = obtener_saldo_usdt(bot_exchange)
    if saldo_inicial is None:
        log("[ADVERTENCIA] No se pudo leer el saldo USDT de Binance. "
            "Las compras estaran deshabilitadas hasta que el saldo sea detectable.")
    elif saldo_inicial < MIN_TRADE_USDT:
        log(f"[ADVERTENCIA] Tienes ${saldo_inicial:.2f} USDT disponibles.")
        log(f"Necesitas al menos ${MIN_TRADE_USDT:.2f} USDT para comprar. "
            "Las compras estaran deshabilitadas.")
        log("[ADVERTENCIA] Si hay posiciones abiertas, el bot seguira gestionandolas "
            "normalmente (SL, TP, trailing).")
        log("[ADVERTENCIA] Recarga USDT en la cuenta cuando puedas para reanudar compras.")
    else:
        log(f"[VERIFICACION] Saldo inicial validado: ${saldo_inicial:,.2f} USDT.")

    # Verificar y limpiar ordenes abiertas que no son del bot
    _limpiar_ordenes_abiertas(bot_exchange, estado_bot)

    # Asegurar combustible inicial del 5% en BNB
    rebalancear_combustible_bnb(bot_exchange, estado_bot)

    # Iniciar WebSocket manager para datos en tiempo real
    global ws_manager
    if WS_DISPONIBLE:
        ws_manager = WSManager(SYMBOL_LIST)
        ws_manager.start(exchange=bot_exchange)
        log("[SISTEMA] WebSocket manager iniciado (klines + precios + user data).")
    else:
        log("[SISTEMA] WebSocket no disponible. Usando REST polling.")

    log("[SISTEMA] Bot operativo. Dashboard en vivo.")

    # Detectar posiciones abiertas de sesiones anteriores
    pos_abiertas = {s: p for s, p in estado_bot["posiciones"].items() if p is not None}
    if pos_abiertas:
        log(f"[RECUPERACION] Posiciones abiertas detectadas (se mantienen):")
        for sym, pos in pos_abiertas.items():
            base = sym.split('/')[0]
            pc = pos.get("precio_compra", 0)
            monto = pos.get("monto_invertido", 0)
            sl = pos.get("stop_loss", 0)
            tp = pos.get("take_profit", 0)
            log(f"  {sym}: ${monto:.2f} @ ${pc:.2f} | SL=${sl:.2f} TP=${tp:.2f}")

    while True:
        try:
            _purgar_logs()

            # Refrescar MIN_NOTIONAL_USDT desde API cada 30 min por si Binance cambia filtros
            ahora = time.time()
            if ahora - _ULTIMA_ACT_REFRESH_NOTIONAL > _INTERVALO_REFRESH_NOTIONAL:
                _cargar_min_notional(bot_exchange)
                _ULTIMA_ACT_REFRESH_NOTIONAL = ahora

            # Calcular capital total una sola vez por ciclo para ahorrar peticiones a la API
            cap = calcular_capital_total(bot_exchange, estado_bot)

            ejecutar_ciclo_trading(bot_exchange, estado_bot, cap)

            _guardar_metricas(cap.get("total_cuenta"))

            # Redibujar el dashboard completo (limpia pantalla + redibuja)
            dibujar_dashboard(bot_exchange, estado_bot, cap)

            # Pausa entre ciclos
            time.sleep(LOOP_SLEEP_SEGUNDOS)

        except KeyboardInterrupt:
            log("[SISTEMA] Detencion manual (Ctrl+C). Guardando estado y saliendo.")
            guardar_estado(estado_bot)
            break
        except Exception as e:
            log(f"[ALERTA CRITICA EN BUCLE]: {e}")
            log(traceback.format_exc().strip())
            time.sleep(LOOP_SLEEP_SEGUNDOS)


if __name__ == "__main__":
    main()
