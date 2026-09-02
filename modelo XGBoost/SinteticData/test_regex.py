import re

linea = r'[2026-03-21 16:00:00] [SENAL COMPRA] ETH/USDT | RSI=39.5 (umbral 40) | volatilidad 7d=5.36% | TP=4.29% | precio=$2146.65 | invirtiendo $5.00'

# En raw string, \$ no es necesario, solo $
r = r'precio=\$(?P<precio>[0-9.]+)'
print('Regex:', repr(r))
print('Match:', re.search(r, linea))

# Si no funciona, probar sin el dollar
r2 = r'precio=(?P<precio>[0-9.]+)'
print('Regex2:', repr(r2))
print('Match2:', re.search(r2, linea))