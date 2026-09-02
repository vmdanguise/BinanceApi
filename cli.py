# -*- coding: utf-8 -*-
"""CLI de consulta rapida del estado del bot (sin conexion a Binance)."""

import json
import os
import sys
import time
from datetime import datetime

from dotenv import load_dotenv
try:
    import msvcrt
    _HAY_TECLADO = True
except ImportError:
    _HAY_TECLADO = False

BASE = os.path.dirname(os.path.abspath(__file__))
ESTADO_PATH = os.path.join(BASE, "estado_bot.json")
METRICAS_PATH = os.path.join(BASE, "metricas.json")
LOG_DIR = os.path.join(BASE, "logs")
PID_PATH = os.path.join(BASE, "bot.pid")

CONFIG_PATH = os.path.join(BASE, "conf", "bot.properties")


def _leer_sma_dist():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if linea.startswith("SMA_DISTANCIA_MAX_PCT") and "=" in linea:
                    val = linea.split("=", 1)[1].split("#")[0].strip()
                    return float(val)
    except (OSError, ValueError):
        pass
    return 0.05


def _cargar_env():
    env_selected = "desa.env"
    cfg_path = os.path.join(BASE, "conf", "bot.properties")
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
    env_path = os.path.join(BASE, "conf", env_selected)
    load_dotenv(env_path)


def _bot_ejecutando():
    if not os.path.exists(PID_PATH):
        return False
    try:
        with open(PID_PATH, "r") as f:
            pid = int(f.read().strip())
        if sys.platform == 'win32':
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x400, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        os.kill(pid, 0)
        return True
    except (ValueError, OSError, ProcessLookupError):
        return False


def _leer_estado():
    if not os.path.exists(ESTADO_PATH):
        return None
    try:
        with open(ESTADO_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _leer_metricas():
    if not os.path.exists(METRICAS_PATH):
        return None
    try:
        with open(METRICAS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _ultimas_lineas_log(n=3):
    if not os.path.isdir(LOG_DIR):
        return []
    logs = [f for f in os.listdir(LOG_DIR) if f.startswith("bot_trading_") and f.endswith(".log")]
    if not logs:
        return []
    logs.sort(reverse=True)
    ruta = os.path.join(LOG_DIR, logs[0])
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            lineas = f.readlines()
        return [l.strip() for l in lineas[-n:] if l.strip()]
    except OSError:
        return []


def _color(texto, codigo):
    return f"\033[{codigo}m{texto}\033[0m"


def _fmt_rsi(val, rsi_ent, rsi_sob):
    if val is None:
        return _color("RSI s/d", "90")
    col = "92" if val <= rsi_ent else ("91" if val >= rsi_sob else "93")
    return _color(f"RSI {val:.0f}", col) + _color(f" (E<{rsi_ent:.0f} S>{rsi_sob:.0f})", "90")


def _esperar_o_escape(segundos):
    """Espera N segundos, revisando teclado cada 0.5s. Retorna True si se presiono Escape."""
    if not _HAY_TECLADO:
        time.sleep(segundos)
        return False
    fin = time.time() + segundos
    while time.time() < fin:
        if msvcrt.kbhit():
            tecla = msvcrt.getch()
            if tecla == b'\x1b':
                return True
        time.sleep(0.5)
    return False


def main():
    _cargar_env()

    rsi_entrada = {"BTC/USDT": 30, "ETH/USDT": 40}
    rsi_sobrecompra = {"BTC/USDT": 70, "ETH/USDT": 70}
    SMA_DIST = _leer_sma_dist()

    while True:
        estado = _leer_estado()
        metricas = _leer_metricas()

        # Limpiar pantalla
        if sys.platform == 'win32':
            os.system('cls')
        else:
            os.system('clear')

        entorno = os.getenv("ENTORNO", "?").upper()
        capital = ((metricas or {}).get("_total_cuenta") or
                   (estado or {}).get("capital_inicio_dia") or 0)
        balance = (estado or {}).get("balance_trades", 0.0)
        trades_hoy = (estado or {}).get("trades_executed_today", 0)
        drawdown = (estado or {}).get("drawdown_max_del_dia", 0.0)
        min_trade = (metricas or {}).get("_min_trade_usdt", 5.0)
        min_notional = (metricas or {}).get("_min_notional_usdt")

        corriendo = _bot_ejecutando()
        estado_bot_str = _color("EN EJECUCION", "92") if corriendo else _color("DETENIDO", "91")
        print(f"  {_color('BOT TRADING v4.6', '93')}  {_color(f'[{entorno}]', '92')}  {estado_bot_str}")
        print(f"  Capital: {_color(f'${capital:,.2f}', '96')}  "
              f"P&L: {_color(f'{balance:+.2f}', '92' if balance >= 0 else '91')}  "
              f"Trades hoy: {_color(str(trades_hoy), '93')}  "
              f"Inversion: {_color(f'${min_trade:.1f}', '90')}  "
              f"MinNotional: {_color(f'${min_notional:.2f}', '90') if min_notional else 'N/A'}")
        print(f"  Drawdown: {_color(f'{drawdown*100:.1f}%', '90') if drawdown else _color('0.0%', '90')}")
        tc = (metricas or {}).get("_total_cuenta")
        ci = (estado or {}).get("capital_inicio_dia")
        if tc and ci:
            dif = tc - ci - balance
            if abs(dif) > 1:
                print(f"  {_color(f'[AVISO] Saldo externo vario ${dif:+.2f} (testnet inestable)', '90')}")
        print("")

        posiciones = (estado or {}).get("posiciones", {})
        cooldowns = (estado or {}).get("cooldown_until", {})
        ahora = datetime.now()

        for sym in ["BTC/USDT", "ETH/USDT"]:
            pos = posiciones.get(sym)
            m = (metricas or {}).get(sym, {})
            estado_sym = "EN POSICION" if pos else "LIQUIDO"
            color_sym = "93" if pos else "92"

            cd = cooldowns.get(sym)
            cd_str = "libre"
            if cd:
                try:
                    cd_fecha = datetime.fromisoformat(cd)
                    if cd_fecha > ahora:
                        resto = (cd_fecha - ahora).total_seconds()
                        cd_str = f"{resto/3600:.1f}h"
                    else:
                        cd_str = "libre"
                except (ValueError, TypeError):
                    cd_str = "?"

            precio = m.get("precio") or 0
            sma = m.get("sma200")
            if sma and precio:
                sma_ok = precio >= sma * (1 - SMA_DIST)
                sma_str = _color("SMA200 bueno", "92") if sma_ok else _color(f"SMA200 {(precio/sma-1)*100:.1f}%", "91")
            else:
                sma_str = _color("SMA200 s/d", "90")
            ema = m.get("ema50")
            if ema and precio:
                ema_ok = precio >= ema * (1 - SMA_DIST)
                ema_str = _color("EMA50 bueno", "92") if ema_ok else _color(f"EMA50 {(precio/ema-1)*100:.1f}%", "91")
            else:
                ema_str = _color("EMA50 s/d", "90")
            rsi = m.get("rsi")
            rsi_str = _fmt_rsi(rsi, rsi_entrada.get(sym, 30), rsi_sobrecompra.get(sym, 70))

            vol = m.get("vol")
            vol_str = _color(f"vol {vol*100:.1f}%" if vol else "", "90")
            sl_pct = m.get("sl")
            sl_str = _color(f"SL {sl_pct*100:.2f}%" if sl_pct else "", "90")

            print(f"  {_color(sym, '90')}")
            print(f"    Estado: {_color(estado_sym, color_sym)}  "
                  f"{rsi_str}  {sma_str}  {ema_str}")
            if pos:
                pc = pos.get("precio_compra", 0)
                precio_actual = m.get("precio", 0) or pc
                pnl = ((precio_actual / pc - 1) * 100) if pc > 0 else 0
                pnl_col = "92" if pnl >= 0 else "91"
                monto_inv = pos.get("monto_invertido", 0)
                sl_pos = pos.get("stop_loss", 0)
                tp_pos = pos.get("take_profit", 0)
                fe = pos.get("fecha_entrada", "")
                horas = 0.0
                if fe:
                    try:
                        horas = (ahora - datetime.fromisoformat(fe)).total_seconds() / 3600
                    except Exception:
                        pass
                print(f"    Precio: {_color(f'${precio_actual:.2f}', pnl_col)}  "
                      f"Entrada: ${pc:.2f}  "
                      f"P&L: {_color(f'{pnl:+.2f}%', pnl_col)}  "
                      f"Invertido: ${monto_inv:.2f}  "
                      f"Horas: {horas:.1f}")
                print(f"    Stop Loss: {_color(f'${sl_pos:.2f}', '91')}  "
                      f"Take Profit: {_color(f'${tp_pos:.2f}', '92')}")
                print(f"    Cooldown: {_color(cd_str, '90')}  {vol_str}  {sl_str}")
            else:
                ent = rsi_entrada.get(sym, 30)
                print(f"    Entrada si RSI < {ent}  Cooldown: {_color(cd_str, '90')}  "
                      f"{vol_str}  {sl_str}")
            print("")

        eventos = _ultimas_lineas_log(3)
        if eventos:
            print(f"  {_color('Ultimos eventos:', '90')}")
            for ev in eventos:
                print(f"    {_color(ev, '90')}")
            print("")

        print(f"  {_color('Presiona ESC para salir...', '90')}")
        print("")

        if _esperar_o_escape(10):
            break


if __name__ == "__main__":
    main()
