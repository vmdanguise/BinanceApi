import re

linea = r'[2026-03-21 16:00:00] [SENAL COMPRA] ETH/USDT | RSI=39.5 (umbral 40) | volatilidad 7d=5.36% | TP=4.29% | precio=$2146.65 | invirtiendo $5.00'

RE_SENAL = re.compile(
    r'\[(?P<ts>[^\]]+)\] \[SENAL COMPRA\] (?P<symbol>\w+/USDT) \| '
    r'RSI=(?P<rsi>[\d.]+) \(umbral (?P<rsi_umbral>[\d.]+)\) \| '
    r'volatilidad 7d=(?P<volatilidad>[\d.]+)% \| '
    r'TP=(?P<tp_pct>[\d.]+)% \| precio=\$(?P<precio>[\d.]+) \| '
    r'invirtiendo \$(?P<monto>[\d.]+)'
)

m = RE_SENAL.search(linea)
if m:
    print('MATCH:', m.groupdict())
else:
    print('NO MATCH')
    print('Line:', repr(linea))