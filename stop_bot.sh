#!/bin/bash
cd "$(dirname "$0")"
if [ -f bot.pid ]; then
    PID=$(cat bot.pid)
    kill $PID 2>/dev/null
    rm bot.pid
    echo "Bot detenido (PID $PID)."
else
    echo "Buscando proceso..."
    pkill -f "python3 bot_trading.py" 2>/dev/null
    echo "Hecho."
fi
