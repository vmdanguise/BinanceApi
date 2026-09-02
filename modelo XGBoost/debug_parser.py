import os
from pathlib import Path
import re

BASE = Path(r"D:\BinanceApi")
LOG_DIR = BASE / "logs"

print(f"LOG_DIR: {LOG_DIR}")
print(f"Existe: {LOG_DIR.exists()}")

logs = sorted(
    f for f in os.listdir(LOG_DIR)
    if (f.startswith("bot_trading_") or f.startswith("sintetic_bot_trading_")) and f.endswith(".log")
)

print(f"Logs encontrados: {len(logs)}")
print(f"Primeros 5: {logs[:5]}")
print(f"Ultimos 5: {logs[-5:]}")

# Contar señales en un log sintético
RE_SENAL = re.compile(
    r'\[(?P<ts>[^\]]+)\] \[SENAL COMPRA\] (?P<symbol>\w+/USDT) \| '
    r'RSI=(?P<rsi>[\d.]+) \(umbral (?P<rsi_umbral>[\d.]+)\) \| '
    r'volatilidad 7d=(?P<volatilidad>[\d.]+)% \| '
    r'TP=(?P<tp_pct>[\d.]+)% \| precio=\$(?P<precio>[\d.]+) \| '
    r'invirtiendo \$(?P<monto>[\d.]+)'
)

# Probar con un log sintético específico
test_log = "sintetic_bot_trading_2026-03-21.log"
if test_log in logs:
    ruta = LOG_DIR / test_log
    print(f"\nProbando {test_log}...")
    with open(ruta, "r", encoding="utf-8") as f:
        contenido = f.read()
    
    senales = len(RE_SENAL.findall(contenido))
    print(f"  Señales encontradas: {senales}")
    
    # Mostrar primera línea con SENAL
    for i, linea in enumerate(contenido.split('\n')[:20]):
        if 'SENAL' in linea:
            print(f"  Línea {i}: {linea[:80]}...")
            m = RE_SENAL.search(linea)
            print(f"  Match: {m is not None}")
            break
else:
    print(f"\n{test_log} NO está en la lista de logs!")