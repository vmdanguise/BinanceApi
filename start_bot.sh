#!/bin/bash
cd "$(dirname "$0")"
if [ -f bot.lock ]; then
    echo "Ya hay un inicio en curso. Si el bot no esta corriendo, borre bot.lock."
    exit 1
fi
touch bot.lock
if [ -f bot.pid ]; then
    OLD_PID=$(cat bot.pid)
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "El bot ya esta en ejecucion (PID $OLD_PID)."
        rm -f bot.lock
        exit 1
    fi
fi
if pgrep -f "python3.*bot_trading" > /dev/null 2>&1; then
    echo "El bot ya esta en ejecucion."
    rm -f bot.lock
    exit 1
fi
echo "Starting bot_trading.py in background..."
nohup python3 bot_trading.py > logs/bot_console.log 2>&1 &
PID=$!
echo $PID > bot.pid
rm -f bot.lock
echo "Bot iniciado (PID $PID)"
