"""
WebSocket Manager para Binance Testnet.
Mantiene caches en memoria de precios, klines y balance via WS push.
Elimina la necesidad de polling REST para datos de mercado.
"""

import asyncio
import json
import os
import time
import threading
import websockets


def _log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}][WS] {msg}", flush=True)


def _ws_url():
    """Construye la URL combinada del WS. Se evalua en cada conexion para respetar .env."""
    base = os.getenv("WS_BASE_URL", "wss://stream.testnet.binance.vision")
    return f"{base}/stream"


class WSManager:
    """
    Maneja conexiones WebSocket a Binance y mantiene caches local.
    Thread-safe: los caches se leen desde el hilo principal del bot.
    """

    def __init__(self, symbols):
        self.symbols = symbols

        # --- Caches thread-safe ---
        self._lock = threading.Lock()

        # Klines: symbol -> lista de dicts {t, o, h, l, c, v}
        self._klines = {s: [] for s in symbols}

        # Precios: symbol -> ultimo precio
        self._prices = {s: 0.0 for s in symbols}

        # Balance: currency -> cantidad free
        self._balance = {}

        # Order updates: lista de eventos executionReport
        self._order_updates = []

        # Listen key para user data stream
        self._listen_key = None

        # Estado
        self._running = False
        self._thread = None
        self._ws = None
        self._connected = False
        self._last_message = None

    # ======================================================================
    # PUBLIC API (thread-safe, llama desde el hilo principal)
    # ======================================================================

    def get_klines(self, symbol, limit=210):
        """Devuelve las ultimas N klines como lista de dicts."""
        with self._lock:
            return list(self._klines.get(symbol, [])[-limit:])

    def get_price(self, symbol):
        """Devuelve el ultimo precio conocido."""
        with self._lock:
            return self._prices.get(symbol, 0.0)

    def get_balance(self, currency="USDT"):
        """Devuelve el balance free de una moneda."""
        with self._lock:
            return self._balance.get(currency, 0.0)

    def pop_order_updates(self):
        """Devuelve y limpia la cola de order updates."""
        with self._lock:
            updates = list(self._order_updates)
            self._order_updates.clear()
            return updates

    def is_connected(self):
        """Devuelve True si el WS esta conectado y recibiendo datos en los ultimos 60s."""
        if not self._connected or self._last_message is None:
            return False
        return (time.time() - self._last_message) < 60

    # ======================================================================
    # START
    # ======================================================================

    def start(self, exchange=None):
        """
        Inicia el WS manager en un hilo background.
        Si se provee exchange, obtiene el listenKey para user data stream.
        """
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True,
                                         args=(exchange,), name="ws-manager")
        self._thread.start()

    # ======================================================================
    # INTERNALS
    # ======================================================================

    def _run_loop(self, exchange):
        """Loop principal del hilo WS."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main_loop(exchange))

    async def _main_loop(self, exchange):
        """Reintenta conexion infinitamente."""
        # Obtener listenKey para user data stream y renovarla periódicamente
        if exchange:
            await self._obtener_listen_key(exchange)
            asyncio.create_task(self._renovar_listen_key(exchange))

            # Bootstrapping de klines históricas vía REST para evitar fallback constante en cada ciclo
            loop = asyncio.get_running_loop()
            for sym in self.symbols:
                try:
                    ohlcv = await loop.run_in_executor(
                        None,
                        lambda s=sym: exchange.fetch_ohlcv(s, timeframe='1h', limit=210)
                    )
                    if ohlcv:
                        klines = []
                        for k in ohlcv:
                            klines.append({
                                "t": k[0],
                                "o": float(k[1]),
                                "h": float(k[2]),
                                "l": float(k[3]),
                                "c": float(k[4]),
                                "v": float(k[5]),
                            })
                        with self._lock:
                            self._klines[sym] = klines
                except Exception:
                    pass

        while self._running:
            try:
                await self._conectar_y_escuchar()
            except Exception as e:
                _log(f"Error: {e}. Reintentando en 3s...")
            if self._running:
                _log("Desconectado. Reintentando en 3s...")
                await asyncio.sleep(3)

    async def _conectar_y_escuchar(self):
        """Conecta a WS combined stream y procesa mensajes."""
        streams = []

        # Kline 1h para cada symbol
        for sym in self.symbols:
            s = sym.replace("/", "").lower()
            streams.append(f"{s}@kline_1h")

        # MiniTicker para cada symbol + BNB
        for sym in self.symbols:
            s = sym.replace("/", "").lower()
            streams.append(f"{s}@miniTicker")
        streams.append("bnbusdt@miniTicker")

        # User data stream
        if self._listen_key:
            streams.append(self._listen_key)

        url = f"{_ws_url()}?streams={'/'.join(streams)}"

        async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
            self._ws = ws
            self._connected = True
            _log(f"Conectado ({len(streams)} streams).")

            try:
                while self._running:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=30)
                        self._last_message = time.time()
                        raw = json.loads(msg)
                        data = raw.get("data", raw)
                        await self._procesar_mensaje(data)
                    except asyncio.TimeoutError:
                        continue
            finally:
                self._connected = False

    async def _procesar_mensaje(self, data):
        """Rutea un mensaje WS al handler adecuado."""
        event = data.get("e")

        if event == "kline":
            self._handle_kline(data)
        elif event == "24hrMiniTicker":
            self._handle_mini_ticker(data)
        elif event == "executionReport":
            self._handle_order_update(data)
        elif event == "outboundAccountInfo":
            self._handle_balance_update(data)
        elif event == "balanceUpdate":
            self._handle_balance_update(data)

    def _handle_kline(self, data):
        """Actualiza cache de klines con un evento de vela."""
        k = data.get("k", {})
        if not k:
            return

        symbol = k.get("s", "")
        interval = k.get("i", "")
        if interval != "1h":
            return

        # Encontrar el symbol en nuestro formato
        sym = self._symbol_from_ws(symbol)
        if not sym:
            return

        candle = {
            "t": k.get("t", 0),      # open time
            "o": float(k.get("o", 0)),
            "h": float(k.get("h", 0)),
            "l": float(k.get("l", 0)),
            "c": float(k.get("c", 0)),
            "v": float(k.get("v", 0)),
        }

        with self._lock:
            klines = self._klines[sym]
            if klines and klines[-1]["t"] == candle["t"]:
                klines[-1] = candle
            else:
                klines.append(candle)
                if len(klines) > 500:
                    self._klines[sym] = klines[-300:]

    def _handle_mini_ticker(self, data):
        """Actualiza cache de precios con un miniTicker."""
        symbol = data.get("s", "")
        price = float(data.get("c", 0))
        if price <= 0:
            return

        sym = self._symbol_from_ws(symbol)
        if sym:
            with self._lock:
                self._prices[sym] = price

    def _handle_order_update(self, data):
        """Registra un evento de order fill."""
        update = {
            "symbol": data.get("s", ""),
            "side": data.get("S", ""),
            "type": data.get("o", ""),
            "status": data.get("X", ""),
            "price": float(data.get("p", 0)),
            "executed_qty": float(data.get("z", 0)),
            "cummulative_quote": float(data.get("Z", 0)),
            "order_id": data.get("i", 0),
            "time": data.get("T", 0),
        }
        with self._lock:
            self._order_updates.append(update)

    def _handle_balance_update(self, data):
        """Actualiza cache de balances desde user data stream."""
        # outboundAccountInfo: snapshot completo de todos los balances
        if "B" in data:
            for b in data["B"]:
                currency = b.get("a", "")
                free = float(b.get("f", 0))
                with self._lock:
                    self._balance[currency] = free
            return
        # balanceUpdate: delta sobre un activo especifico
        asset = data.get("a", "")
        delta = float(data.get("d", 0))
        if asset and delta != 0:
            with self._lock:
                actual = self._balance.get(asset, 0.0)
                self._balance[asset] = max(0.0, actual + delta)

    def _symbol_from_ws(self, ws_symbol):
        """Convierte 'BTCUSDT' a 'BTC/USDT'."""
        ws_upper = ws_symbol.upper()
        for sym in self.symbols:
            if sym.replace("/", "").upper() == ws_upper:
                return sym
        return None

    async def _obtener_listen_key(self, exchange):
        """Obtiene el listenKey para user data stream via REST."""
        try:
            # Usar ccxt para hacer el POST
            response = exchange.privatePostUserDataStream()
            self._listen_key = response.get("listenKey")
        except Exception:
            pass

    async def _renovar_listen_key(self, exchange):
        """Renueva el listenKey cada 30 minutos."""
        while self._running:
            await asyncio.sleep(1800)
            if self._listen_key:
                try:
                    exchange.privatePutUserDataStream({"listenKey": self._listen_key})
                except Exception:
                    await self._obtener_listen_key(exchange)
