# -*- coding: utf-8 -*-
"""
Genera logs sinteticos simulando el bot de trading sobre velas historicas de Binance.
Descarga 1 año de velas 1H para BTC/USDT y ETH/USDT, simula la logica del bot
y genera logs con el mismo formato que bot_trading.py.

Los logs se guardan como: logs/sintetic_bot_trading_YYYY-MM-DD.log
"""

import os
import sys
import math
import json
import datetime
import time
from pathlib import Path

import pandas as pd
import numpy as np

try:
    import ccxt
except ImportError:
    print("[ERROR] ccxt no instalado. Ejecutar: py -m pip install ccxt")
    sys.exit(1)

# ──────────────────────── CONFIGURACION ────────────────────────

BASE_DIR = Path(__file__).parent.parent.parent  # D:\BinanceApi
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

SYMBOLS = ["BTC/USDT", "ETH/USDT"]
TIMEFRAME = '1h'
DIAS_HISTORICOS = 365  # 1 año

# Parametros del bot (copiados de bot.properties / bot_trading.py)
RSI_PERIODO = 14
RSI_ENTRADA = {"BTC/USDT": 30, "ETH/USDT": 40}
RSI_SOBRECOMPRA = {"BTC/USDT": 70, "ETH/USDT": 70}

VOLATILIDAD_VENTANA_HORAS = 168  # 7 dias
SL_DINAMICO_MIN = 0.01
SL_DINAMICO_MAX = 0.05
VOLATILIDAD_MULT_SL = 1.0

TP_PCT = 0.0055
TP_MIN_PCT = 0.005
TP_MAX_PCT = 0.05
TP_VOL_MULT = 0.8
TP_DINAMICO_HABILITADO = True

TRAILING_ACTIVA_MIN = 0.001
TRAILING_ACTIVA_MAX = 0.003
TRAILING_DISTANCIA_MIN = 0.001
TRAILING_DISTANCIA_MAX = 0.005

COOLDOWN_MIN_HORAS = 0.0
COOLDOWN_MAX_HORAS = 0.1

MIN_TRADE_USDT = 5.0
MIN_NOTIONAL_USDT = 5.0

BINANCE_FEE_RATE = 0.001  # 0.1% maker

SMA_DISTANCIA_MAX_PCT = 0.05
SMA_FILTR0_HABILITADO = True

MIN_INTERVALO_MANIOBRAS_HORAS = 0.5
MAX_DAILY_TRADES = 20
DRAWDOWN_DIARIO_MAX_PCT = 0.05

TRAILING_ACTIVA_MIN_POS = TRAILING_ACTIVA_MIN
TRAILING_DISTANCIA_MIN_POS = TRAILING_DISTANCIA_MIN

# ──────────────────────── CLASES AUXILIARES ────────────────────────

class Posicion:
    def __init__(self, symbol, precio_compra, cantidad, monto_invertido, sl_pct, tp_pct, t_act, t_dist, cd_hs, fecha):
        self.symbol = symbol
        self.precio_compra = precio_compra
        self.cantidad = cantidad
        self.monto_invertido = monto_invertido
        self.stop_loss = precio_compra * (1 - sl_pct)
        self.take_profit = precio_compra * (1 + tp_pct)
        self.fecha_entrada = fecha
        self.trailing_activa_pct = t_act
        self.trailing_distancia_pct = t_dist
        self.cooldown_horas = cd_hs

    def to_dict(self):
        return {
            "precio_compra": self.precio_compra,
            "cantidad": self.cantidad,
            "monto_invertido": self.monto_invertido,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "fecha_entrada": self.fecha_entrada.isoformat(),
            "trailing_activa_pct": self.trailing_activa_pct,
            "trailing_distancia_pct": self.trailing_distancia_pct,
            "cooldown_horas": self.cooldown_horas,
        }


class EstadoBot:
    def __init__(self):
        self.posiciones = {s: None for s in SYMBOLS}
        self.cooldown_until = {s: None for s in SYMBOLS}
        self.ultima_maniobra_time = {s: datetime.datetime(2000, 1, 1) for s in SYMBOLS}
        self.trades_executed_today = 0
        self.balance_trades = 0.0
        self.capital_inicio_dia = 100.0  # Capital inicial simulado
        self.drawdown_max_del_dia = 0.0
        self.ultima_limpieza_diaria = None


# ──────────────────────── INDICADORES TECNICOS ────────────────────────

def calcular_indicadores(df):
    """Calcula RSI, ATR%, SMA200, EMA50 y metricas adaptativas."""
    if len(df) < 210:
        return None

    try:
        # RSI
        delta = df['close'].diff()
        gain = delta.clip(lower=0).rolling(window=RSI_PERIODO).mean()
        loss = (-delta.clip(upper=0)).rolling(window=RSI_PERIODO).mean()
        rs = gain / loss
        sin_perdidas = (loss == 0) & (gain > 0)
        sin_cambios = (gain == 0) & (loss == 0)
        rs = rs.where(~sin_perdidas, float('inf'))
        rs = rs.where(~sin_cambios, 1.0)
        rsi = 100 - (100 / (1 + rs))

        # ATR porcentual
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(window=14).mean()
        atr_pct = (atr / df['close']) * 100

        # SMA200 y EMA50
        sma200 = df['close'].rolling(window=200).mean()
        ema50 = df['close'].ewm(span=50, adjust=False).mean()

        # Metricas adaptativas (volatilidad 7d)
        metricas = calcular_metricas_adaptativas(df)

        ultimo = len(df) - 1
        rsi_val = rsi.iloc[ultimo] if not pd.isna(rsi.iloc[ultimo]) else None
        atr_val = atr_pct.iloc[ultimo] if not pd.isna(atr_pct.iloc[ultimo]) else None
        close_val = df['close'].iloc[ultimo]
        sma_val = sma200.iloc[ultimo] if not pd.isna(sma200.iloc[ultimo]) else None
        ema_val = ema50.iloc[ultimo] if not pd.isna(ema50.iloc[ultimo]) else None

        if rsi_val is None or atr_val is None:
            return None

        return {
            'rsi': float(rsi_val),
            'atr_pct': float(atr_val),
            'precio': float(close_val),
            'sma200': float(sma_val) if sma_val is not None else None,
            'ema50': float(ema_val) if ema_val is not None else None,
            'metricas': metricas,
        }
    except Exception as e:
        print(f"[ERROR] calculando indicadores: {e}")
        return None


def calcular_metricas_adaptativas(df):
    """Calcula SL, TP, trailing, cooldown basados en volatilidad 7d."""
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

    # Momento alcista (ultimas 4 velas suben)
    momento_alcista = False
    if len(df) >= 5:
        closes = df['close'].iloc[-5:-1]
        momento_alcista = all(closes.iloc[i] > closes.iloc[i-1] for i in range(1, 4))

    return {
        "vol": vol,
        "sl": sl,
        "t_act": t_act,
        "t_dist": t_dist,
        "cd": cd,
        "tp": tp,
        "momento_alcista": momento_alcista,
    }


# ──────────────────────── SIMULADOR ────────────────────────

def precalcular_rsi(df, periodo=RSI_PERIODO):
    """Precalcula RSI sobre todo el dataframe de una vez (vectorizado)."""
    delta = df['close'].diff()
    gain = delta.clip(lower=0).rolling(window=periodo).mean()
    loss = (-delta.clip(upper=0)).rolling(window=periodo).mean()
    rs = gain / loss
    sin_perdidas = (loss == 0) & (gain > 0)
    sin_cambios = (gain == 0) & (loss == 0)
    rs = rs.where(~sin_perdidas, float('inf'))
    rs = rs.where(~sin_cambios, 1.0)
    rsi = 100 - (100 / (1 + rs))
    df = df.copy()
    df['rsi'] = rsi
    return df


class SimuladorBot:
    def __init__(self, exchange):
        self.exchange = exchange
        self.estado = EstadoBot()
        self.estado.ultima_limpieza_diaria = datetime.datetime.now()
        self.log_file = None
        self.log_date = None
        self.current_timestamp = None
        self.rsi_5m_hist = {s: [] for s in SYMBOLS}  # historial RSI 5m

    def log(self, mensaje):
        """Escribe en el log con formato igual al bot real."""
        ts = self.current_timestamp if self.current_timestamp else datetime.datetime.now()
        fecha_str = ts.strftime("%Y-%m-%d")
        
        # Rotar log por fecha
        if fecha_str != self.log_date:
            if self.log_file:
                self.log_file.close()
            self.log_date = fecha_str
            log_path = LOGS_DIR / f"sintetic_bot_trading_{fecha_str}.log"
            self.log_file = open(log_path, "a", encoding="utf-8")
            print(f"[LOG] Escribiendo en: {log_path} ({fecha_str})")

        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
        linea = f"[{ts_str}] {mensaje}"
        self.log_file.write(linea + "\n")
        self.log_file.flush()

    def ejecutar_simulacion(self, datos_1h, rsi_5m_precalc=None):
        """Ejecuta la simulacion ciclo por ciclo sobre velas 1h."""
        # Obtener todas las timestamps comunes
        timestamps = None
        for symbol, df in datos_1h.items():
            ts_set = set(df['timestamp'].values)
            if timestamps is None:
                timestamps = ts_set
            else:
                timestamps &= ts_set
        
        timestamps = sorted(list(timestamps))
        print(f"\n[SIMULACION] {len(timestamps)} velas comunes para simular.")

        # Pre-calcular RSI en velas 1h tambien (acelera)
        rsi_1h_precalc = {}
        for symbol in SYMBOLS:
            df_1h = precalcular_rsi(datos_1h[symbol], RSI_PERIODO)
            rsi_1h_precalc[symbol] = dict(zip(df_1h['timestamp'], df_1h['rsi']))

        for i, ts in enumerate(timestamps):
            ahora = pd.Timestamp(ts).to_pydatetime()
            self.current_timestamp = ahora
            self.estado.ultima_limpieza_diaria = ahora
            
            # Reset diario
            if i > 0:
                ts_prev = pd.Timestamp(timestamps[i-1]).to_pydatetime()
                if ahora.date() > ts_prev.date():
                    self.estado.trades_executed_today = 0
                    self.estado.drawdown_max_del_dia = 0.0
                    self.estado.capital_inicio_dia = 100.0 + self.estado.balance_trades
                    self.log(f"[SISTEMA] Reset diario: contador de trades a 0.")

            # Calcular indicadores para cada simbolo
            datos = {}
            for symbol in SYMBOLS:
                df = datos_1h[symbol]
                idx = df[df['timestamp'] == ts].index[0]
                df_hasta = df.loc[:idx]
                ind = calcular_indicadores(df_hasta)
                if ind:
                    datos[symbol] = ind

                # Actualizar historial RSI_5m
                if rsi_5m_precalc and symbol in rsi_5m_precalc:
                    # Buscar RSI_5m desde la ultima vela 5m de esta hora
                    rsi_vals = [v for k, v in rsi_5m_precalc[symbol].items()
                                if k <= ts and not pd.isna(v)]
                    if rsi_vals:
                        rsi_5m = rsi_vals[-1]
                        self.rsi_5m_hist[symbol].append(rsi_5m)
                        # Mantener ultimas 24 (2 horas de RSI_5m)
                        if len(self.rsi_5m_hist[symbol]) > 24:
                            self.rsi_5m_hist[symbol].pop(0)
                if ind:
                    datos[symbol] = ind

            if not datos:
                continue

            # PASO 1: Evaluar ventas (posiciones abiertas)
            for symbol in SYMBOLS:
                posicion = self.estado.posiciones[symbol]
                if posicion is None:
                    continue
                
                d = datos.get(symbol)
                if not d:
                    continue

                precio_actual = d['precio']
                precio_compra = posicion.precio_compra
                stop_loss = posicion.stop_loss
                take_profit = posicion.take_profit
                fecha_entrada = posicion.fecha_entrada
                t_act = posicion.trailing_activa_pct
                t_dist = posicion.trailing_distancia_pct
                cd_hs = posicion.cooldown_horas

                tp_pct = (take_profit / precio_compra - 1)
                momento_alcista = d.get('metricas', {}).get('momento_alcista', False) if d.get('metricas') else False
                
                min_desde_entrada = (ahora - fecha_entrada).total_seconds() / 60
                pnl_actual = (precio_actual / precio_compra - 1)

                # 1. Stop Loss
                if precio_actual <= stop_loss:
                    self.log(f"[VENTA-STOP_LOSS] {symbol}: precio ${precio_actual:.2f} <= SL ${stop_loss:.2f}. Vendiendo.")
                    pnl = posicion.cantidad * precio_actual * (1 - BINANCE_FEE_RATE) - posicion.monto_invertido
                    self.estado.balance_trades += pnl
                    self.estado.posiciones[symbol] = None
                    self.estado.cooldown_until[symbol] = ahora + datetime.timedelta(hours=cd_hs)
                    self.log(f"[BALANCE] {symbol}: PnL {pnl:+.2f}  balance ${self.estado.balance_trades:.2f}")
                    continue

                # Trailing stop
                nuevo_piso = precio_actual * (1 - t_dist)
                if precio_actual > precio_compra * (1 + t_act) and nuevo_piso > stop_loss:
                    posicion.stop_loss = nuevo_piso
                    stop_loss = nuevo_piso
                    self.log(f"[TRAILING] {symbol}: SL elevado a ${stop_loss:.2f} (precio ${precio_actual:.2f}).")

                # 2. Take Profit
                if precio_actual >= take_profit:
                    if momento_alcista:
                        self.log(f"[MOMENTO] {symbol}: TP {tp_pct*100:.2f}% alcanzado pero con momento alcista. Manteniendo.")
                    else:
                        self.log(f"[VENTA-TAKE_PROFIT] {symbol}: precio ${precio_actual:.2f} >= TP ${take_profit:.2f}. Vendiendo.")
                        pnl = posicion.cantidad * precio_actual * (1 - BINANCE_FEE_RATE) - posicion.monto_invertido
                        self.estado.balance_trades += pnl
                        self.estado.posiciones[symbol] = None
                        self.log(f"[BALANCE] {symbol}: PnL {pnl:+.2f}  balance ${self.estado.balance_trades:.2f}")
                    continue

                # 3. Timeout 60min
                if min_desde_entrada >= 60 and pnl_actual > 0.0035 and pnl_actual < tp_pct:
                    if momento_alcista:
                        self.log(f"[MOMENTO] {symbol}: Timeout {min_desde_entrada:.0f}min con ganancia {pnl_actual*100:.2f}% pero momento alcista. Difiriendo.")
                    else:
                        self.log(f"[VENTA-TIMEOUT_60MIN] {symbol}: {min_desde_entrada:.0f}min, ganancia {pnl_actual*100:.2f}% (min 0.35%) < TP {tp_pct*100:.2f}%. Vendiendo.")
                        pnl = posicion.cantidad * precio_actual * (1 - BINANCE_FEE_RATE) - posicion.monto_invertido
                        self.estado.balance_trades += pnl
                        self.estado.posiciones[symbol] = None
                        self.log(f"[BALANCE] {symbol}: PnL {pnl:+.2f}  balance ${self.estado.balance_trades:.2f}")
                    continue

                # 4. SMA200 filtro
                sma200 = d.get('sma200')
                if sma200 and SMA_FILTR0_HABILITADO and precio_actual < sma200 * (1 - SMA_DISTANCIA_MAX_PCT):
                    self.log(f"[VENTA-SMA200] {symbol}: precio ${precio_actual:.2f} < SMA200 ${sma200:.2f}. Vendiendo (anticuchillo).")
                    pnl = posicion.cantidad * precio_actual * (1 - BINANCE_FEE_RATE) - posicion.monto_invertido
                    self.estado.balance_trades += pnl
                    self.estado.posiciones[symbol] = None
                    self.estado.cooldown_until[symbol] = ahora + datetime.timedelta(hours=cd_hs)
                    self.log(f"[BALANCE] {symbol}: PnL {pnl:+.2f}  balance ${self.estado.balance_trades:.2f}")
                    continue

            # PASO 2: Evaluar compras
            candidatos = []
            for symbol in SYMBOLS:
                d = datos.get(symbol)
                if not d:
                    continue
                
                if self.estado.posiciones.get(symbol) is not None:
                    continue

                if self.estado.trades_executed_today >= MAX_DAILY_TRADES:
                    continue

                precio_actual = d['precio']
                sma200 = d.get('sma200')
                rsi = d['rsi']
                metricas = d.get('metricas')

                # Filtro intervalo maniobras
                ultima_m = self.estado.ultima_maniobra_time[symbol]
                if (ahora - ultima_m).total_seconds() / 3600 < MIN_INTERVALO_MANIOBRAS_HORAS:
                    continue

                # Filtro cooldown
                cd = self.estado.cooldown_until[symbol]
                if cd and ahora < cd:
                    continue

                # Filtro SMA200 (anticuchillo)
                ma = sma200 if sma200 else d.get('ema50')
                if ma and SMA_FILTR0_HABILITADO:
                    distancia_ma = (ma - precio_actual) / ma
                    if distancia_ma > SMA_DISTANCIA_MAX_PCT:
                        continue

                # Filtro RSI entrada
                rsi_ent = RSI_ENTRADA.get(symbol, 30)
                if rsi >= rsi_ent:
                    continue

                # Calcular score
                if metricas:
                    sl_pct = metricas['sl']
                    tp_pct_cand = metricas.get('tp', TP_PCT)
                else:
                    sl_pct = 0.02
                    tp_pct_cand = TP_PCT

                score = (tp_pct_cand / sl_pct) if sl_pct > 0 else 0

                candidatos.append({
                    'symbol': symbol,
                    'score': score,
                    'sl_pct': sl_pct,
                    'tp_pct': tp_pct_cand,
                    'rsi_ent': rsi_ent,
                    'rsi': rsi,
                    'metricas': metricas,
                    'precio': precio_actual,
                })

            if candidatos:
                mejor = max(candidatos, key=lambda c: c['score'])
                symbol = mejor['symbol']
                sl_pct = mejor['sl_pct']
                tp_pct = mejor['tp_pct']
                rsi_ent = mejor['rsi_ent']
                rsi = mejor['rsi']
                metricas = mejor['metricas']
                precio_actual = mejor['precio']

                t_act = metricas['t_act'] if metricas else TRAILING_ACTIVA_MIN
                t_dist = metricas['t_dist'] if metricas else TRAILING_DISTANCIA_MIN
                cd_hs = metricas['cd'] if metricas else COOLDOWN_MIN_HORAS

                # Calcular monto
                monto_usdt = MIN_TRADE_USDT
                cantidad = monto_usdt / precio_actual

                # Log señal
                vol_log = f"volatilidad 7d={metricas['vol']*100:.2f}%" if metricas else "volatilidad=s/d"
                # RSI_5m trend
                rsi_5m_vals = self.rsi_5m_hist.get(symbol, [])
                rsi_5m_actual = rsi_5m_vals[-1] if rsi_5m_vals else rsi
                rsi_5m_slope = 0.0
                if len(rsi_5m_vals) >= 3:
                    try:
                        from scipy import stats as sp_stats
                        n = min(12, len(rsi_5m_vals))
                        y = rsi_5m_vals[-n:]
                        rsi_5m_slope = sp_stats.linregress(range(n), y).slope
                    except: pass
                
                # Agregar RSI_5m al log
                rsi_5m_log = f"RSI_5m={rsi_5m_actual:.1f} slope={rsi_5m_slope:+.4f}"

                self.log(f"[SENAL COMPRA] {symbol} | RSI={rsi:.1f} (umbral {rsi_ent:.0f}) | "
                        f"{vol_log} | {rsi_5m_log} | "
                        f"TP={tp_pct*100:.2f}% | precio=${precio_actual:.2f} | invirtiendo ${monto_usdt:.2f}")

                # Ejecutar compra (simulada)
                precio_fill = precio_actual
                fee = monto_usdt * BINANCE_FEE_RATE
                
                self.log(f"[COMPRA MAKER OK] {symbol} | {cantidad:.6f} unidades a ${precio_fill:.2f} (${monto_usdt:.2f})")
                
                take_profit_real = precio_fill * (1 + tp_pct)
                stop_loss_real = precio_fill * (1 - sl_pct)
                
                self.estado.posiciones[symbol] = Posicion(
                    symbol=symbol,
                    precio_compra=precio_fill,
                    cantidad=cantidad,
                    monto_invertido=monto_usdt,
                    sl_pct=sl_pct,
                    tp_pct=tp_pct,
                    t_act=t_act,
                    t_dist=t_dist,
                    cd_hs=cd_hs,
                    fecha=ahora,
                )
                self.estado.ultima_maniobra_time[symbol] = ahora
                self.estado.trades_executed_today += 1
                
                self.log(f"[COMPRA OK] {symbol} | {cantidad:.6f} unidades (${monto_usdt:.2f}) | "
                        f"SL=${stop_loss_real:.2f} TP=${take_profit_real:.2f} | "
                        f"Fee: ${fee:.4f} (0.075% maker simulado) | balance ${self.estado.balance_trades:+.2f}")

        # ── CIERRE FORZOSO DE POSICIONES ABIERTAS ──
        # Al finalizar la simulación, cerrar todas las posiciones con venta al precio actual
        print(f"\n[CIERRE] Cerrando posiciones abiertas restantes...")
        for symbol in SYMBOLS:
            posicion = self.estado.posiciones[symbol]
            if posicion is not None:
                # Usar el último timestamp + 1 hora
                ts_cierre = self.current_timestamp + datetime.timedelta(hours=1) if self.current_timestamp else datetime.datetime.now()
                self.current_timestamp = ts_cierre
                
                # Obtener precio de cierre del último candle
                df_symbol = datos_por_symbol.get(symbol)
                if df_symbol is not None and len(df_symbol) > 0:
                    precio_cierre = float(df_symbol['close'].iloc[-1])
                else:
                    precio_cierre = posicion.precio_compra  # Fallback
                
                # Calcular PnL
                pnl = posicion.cantidad * precio_cierre * (1 - BINANCE_FEE_RATE) - posicion.monto_invertido
                self.estado.balance_trades += pnl
                
                self.log(f"[VENTA-CIERRE_SIMULACION] {symbol}: {posicion.cantidad} {symbol.split('/')[0]} @ ${precio_cierre:.2f} = ${posicion.cantidad * precio_cierre:.2f} | Fee: ${posicion.cantidad * precio_cierre * BINANCE_FEE_RATE:.4f}")
                self.log(f"[BALANCE] {symbol}: PnL {pnl:+.2f}  balance ${self.estado.balance_trades:.2f}")
                
                self.estado.posiciones[symbol] = None
        
        print(f"\n[SIMULACION] Finalizada. Balance final: ${self.estado.balance_trades:+.2f}")
        trades = self.estado.trades_executed_today
        print(f"[SIMULACION] Trades ejecutados (ultimo dia): {trades}")
        
        # Cierre de archivo de log (DESPUES del cierre de posiciones)
        if self.log_file:
            self.log_file.close()


# ──────────────────────── MAIN ────────────────────────

def _descargar_velas(exchange, symbol, timeframe, dias):
    """Descarga velas de Binance con paginacion."""
    todas = []
    since = exchange.parse8601((datetime.datetime.now() - datetime.timedelta(days=dias)).isoformat())
    while True:
        velas = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=1000)
        if not velas:
            break
        todas.extend(velas)
        if len(velas) < 1000:
            break
        since = velas[-1][0] + 1
        time.sleep(0.3)
    df = pd.DataFrame(todas, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df


def main():
    print("=== Generador de Logs Sinteticos ===\n")
    
    exchange = ccxt.binance()
    exchange.load_markets()
    
    print(f"[1/3] Descargando {DIAS_HISTORICOS} dias de velas...")
    
    # Descargar velas 5m (para RSI_5m y slope)
    datos_5m = {}
    for symbol in SYMBOLS:
        print(f"  Descargando {symbol} 5m...")
        datos_5m[symbol] = _descargar_velas(exchange, symbol, '5m', DIAS_HISTORICOS)
        print(f"  ==> {len(datos_5m[symbol])} velas 5m")
    
    # Precalcular RSI_5m sobre todo el dataset
    rsi_5m_precalc = {}
    for symbol in SYMBOLS:
        df_5m = precalcular_rsi(datos_5m[symbol], RSI_PERIODO)
        rsi_5m_precalc[symbol] = dict(zip(df_5m['timestamp'], df_5m['rsi']))
    
    # Resamplear a 1h para la simulacion principal
    datos_1h = {}
    for symbol in SYMBOLS:
        df = datos_5m[symbol].copy()
        df = df.set_index('timestamp')
        # Resample: OHLC
        ohlc = {
            'open': 'first', 'high': 'max', 'low': 'min',
            'close': 'last', 'volume': 'sum'
        }
        df_1h = df.resample('1h', label='right', closed='right').agg(ohlc).dropna()
        df_1h = df_1h.reset_index()
        datos_1h[symbol] = df_1h
        print(f"  Resampleado: {len(df_1h)} velas 1h para {symbol}")

    print(f"\n[2/3] Iniciando simulacion...")
    simulador = SimuladorBot(exchange)
    simulador.ejecutar_simulacion(datos_1h, rsi_5m_precalc)

    print(f"\n[3/3] Listo. Logs guardados en: {LOGS_DIR}")
    print(f"    Para entrenar el modelo, copia los logs sinteticos a ../logs/ o ajusta el parser.")


if __name__ == "__main__":
    main()