"""Real-time BTC/SOL price data from Binance WebSocket."""

import json
import time
import threading
import logging
import requests
from collections import deque

logger = logging.getLogger(__name__)

BINANCE_WS = "wss://stream.binance.com:9443/ws"
SYMBOLS = {"btc": "btcusdt"}


class PriceFeed:
    def __init__(self, max_candles=100):
        # store full candle dictionaries (high, low, close) instead of just a
        # float. this allows downstream code (ATR, regime detection, etc.) to
        # access all OHLC values while still exposing a convenient list of
        # close prices for legacy consumers.
        self.prices = {sym: deque(maxlen=max_candles) for sym in SYMBOLS}
        self.volumes = {sym: deque(maxlen=max_candles) for sym in SYMBOLS}
        self.latest = {sym: 0.0 for sym in SYMBOLS}
        self._last_update = {sym: 0.0 for sym in SYMBOLS}
        self._running = False
        self._thread = None

    def start(self):
        if self._running:
            return

        # Load historical data before starting WebSocket
        self._load_historical_data()

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Price feed started")

    def stop(self):
        self._running = False

    def _run(self):
        import websocket

        streams = "/".join(f"{s}@kline_1m" for s in SYMBOLS.values())
        url = f"{BINANCE_WS}/{streams}"

        while self._running:
            try:
                ws = websocket.WebSocket()
                ws.settimeout(10)
                ws.connect(url)
                logger.info(f"Connected to Binance WS: {url}")

                while self._running:
                    try:
                        raw = ws.recv()
                    except Exception:
                        break

                    try:
                        msg = json.loads(raw)
                        kline = msg.get("k", {})
                        symbol = kline.get("s", "").lower()
                        high = float(kline.get("h", 0))
                        low = float(kline.get("l", 0))
                        close = float(kline.get("c", 0))
                        volume = float(kline.get("v", 0))
                        is_closed = kline.get("x", False)

                        # Map back to our symbol names
                        for name, binance_sym in SYMBOLS.items():
                            if symbol == binance_sym:
                                self.latest[name] = close
                                self._last_update[name] = time.time()
                                if is_closed:
                                    # append a dict with full OHLC so ATR/etc can be
                                    # computed later
                                    self.prices[name].append(
                                        {
                                            "high": high,
                                            "low": low,
                                            "close": close,
                                        }
                                    )
                                    self.volumes[name].append(volume)
                                break
                    except (KeyError, ValueError):
                        continue

                ws.close()
            except Exception as e:
                logger.error(f"Price feed error: {e}")
                time.sleep(5)

    def _load_historical_data(self):
        """Load 100 candles of historical data from Binance REST API before WebSocket starts."""
        try:
            # Fetch 100 minutes of BTC data
            response = requests.get(
                "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=100"
            )
            response.raise_for_status()
            klines = response.json()

            for kline in klines:
                # kline format: [open, high, low, close, ...]
                high = float(kline[2])
                low = float(kline[3])
                close_price = float(kline[4])  # Close price
                volume = float(kline[5])  # Volume

                # Fill BTC data with candle dict
                self.prices["btc"].append(
                    {
                        "high": high,
                        "low": low,
                        "close": close_price,
                    }
                )
                self.volumes["btc"].append(volume)
                self.latest["btc"] = close_price

            logger.info(f"Loaded {len(self.prices['btc'])} historical BTC candles")

        except Exception as e:
            logger.error(f"Failed to load historical data: {e}")

    def get_signals(self, symbol: str) -> dict:
        """Get current price signals for a symbol."""
        sym = symbol.lower()
        if sym not in self.prices:
            return {"prices": [], "volumes": [], "latest": 0}

        stale = (time.time() - self._last_update.get(sym, 0)) > 60
        candles = list(self.prices[sym])
        # legacy list of closes for backwards compatibility
        closes = [c.get("close", 0) if isinstance(c, dict) else c for c in candles]
        return {
            # callers which previously expected floats will still see it here
            "prices": closes,
            # new code can look at "candles" for full OHLC
            "candles": candles,
            "volumes": list(self.volumes[sym]),
            "latest": self.latest.get(sym, 0),
            "stale": stale,
        }


# Singleton
_feed = None


def get_feed() -> PriceFeed:
    global _feed
    if _feed is None:
        _feed = PriceFeed()
    return _feed
