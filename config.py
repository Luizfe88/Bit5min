"""
Polymarket Bot Arena Configuration
"""

import os
from pathlib import Path

def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return

    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export ") :].strip()
            value = value.strip().strip('"').strip("'").strip()
            if not key:
                continue
            if key not in os.environ:
                os.environ[key] = value
    except Exception:
        return

_load_dotenv()

def _env_float(name: str, default: float) -> float:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except ValueError:
        return default


# Trading Mode: "paper" (default, uses $SIM) or "live" (real USDC)
TRADING_MODE = "paper"  # MUST start in paper mode

# Simmer API Configuration
SIMMER_API_KEY_PATH = Path.home() / ".config/simmer/credentials.json"
SIMMER_BASE_URL = "https://api.simmer.markets"

# Multi-agent: each bot gets its own Simmer account for independent trading
# Keys are mapped bot_name -> api_key. Falls back to the default key.
SIMMER_BOT_KEYS_PATH = Path.home() / ".config/simmer/bot_keys.json"

# Polymarket Direct CLOB (for live trading)
POLYMARKET_KEY_PATH = Path.home() / ".config/polymarket/credentials.json"
POLYMARKET_HOST = "https://clob.polymarket.com"
POLYMARKET_CHAIN_ID = 137  # Polygon

# Database
_db_env = os.environ.get("BOT_ARENA_DB_PATH")
DB_PATH = Path(_db_env).expanduser() if _db_env else (Path(__file__).parent / "bot_arena.db")

# Target Markets: Multiple crypto 5-min up/down markets
TARGET_MARKET_QUERIES = ["btc", "eth", "sol", "xrp", "ethereum", "solana", "bitcoin", "ripple"]  # Search terms for market discovery
TARGET_MARKET_KEYWORDS = ["5 min", "5-min", "5min", "up or down", "up/down"]
TARGET_MARKET_NAMES = ["Bitcoin Up or Down", "Ethereum Up or Down", "Solana Up or Down", "Ripple Up or Down", "XRP Up or Down"]  # Alternative market names to search
BTC_5MIN_MARKET_ID = None  # Will be populated by setup.py
ETH_5MIN_MARKET_ID = None  # Ethereum market ID
SOL_5MIN_MARKET_ID = None  # Solana market ID
XRP_5MIN_MARKET_ID = None  # XRP market ID

# Risk Limits - Paper Mode (adjusted for $10000 bankroll)
PAPER_MAX_POSITION = _env_float("BOT_ARENA_PAPER_MAX_POSITION", 1500.0)  # 15% of $10k
PAPER_MAX_DAILY_LOSS_PER_BOT = _env_float("BOT_ARENA_PAPER_MAX_DAILY_LOSS_PER_BOT", 500.0)  # 5% of $10k
PAPER_MAX_DAILY_LOSS_TOTAL = _env_float("BOT_ARENA_PAPER_MAX_DAILY_LOSS_TOTAL", 1500.0)     # 15% of $10k
PAPER_STARTING_BALANCE = _env_float("BOT_ARENA_PAPER_STARTING_BALANCE", 10000.0)              # $10k default bankroll

# Risk Limits - Live Mode (stricter - proportional to $10k bankroll)
LIVE_MAX_POSITION = _env_float("BOT_ARENA_LIVE_MAX_POSITION", 10.0)
LIVE_MAX_DAILY_LOSS_PER_BOT = _env_float("BOT_ARENA_LIVE_MAX_DAILY_LOSS_PER_BOT", 500.0)   # 5% of $10k bankroll
LIVE_MAX_DAILY_LOSS_TOTAL = _env_float("BOT_ARENA_LIVE_MAX_DAILY_LOSS_TOTAL", 1500.0)   # 15% of $10k bankroll

# Dynamic Loss Limits (based on current capital - for moderate risk profile)
MAX_LOSS_PCT_PER_BOT = 0.05    # 5% of current bot capital (moderate/conservative)
MAX_LOSS_PCT_TOTAL = 0.15      # 15% of total current capital (moderate/conservative)

# General Risk Rules (both modes)
MAX_POSITION_PCT_OF_BALANCE = 0.02  # Never bet more than 2% of balance per trade
MAX_TOTAL_POSITION_PCT_OF_BALANCE = 0.50  # Never allocate more than 50% of total balance
MAX_CONSECUTIVE_LOSSES = _env_int("BOT_ARENA_MAX_CONSECUTIVE_LOSSES", 3)  # Pause after 3 consecutive losses
PAUSE_AFTER_CONSECUTIVE_LOSSES_SECONDS = _env_int("BOT_ARENA_PAUSE_AFTER_CONSECUTIVE_LOSSES", 3600)  # Pause for 1 hour
MAX_TRADES_PER_HOUR_PER_BOT = 20  # Hard cap to prevent overtrading in 5-min markets
MIN_TRADE_AMOUNT = _env_float("BOT_ARENA_MIN_TRADE_AMOUNT", 0.01)  # Minimum trade amount

# Evolution Settings
EVOLUTION_INTERVAL_HOURS = 12  # Safety net: máximo 12h sem evolução
EVOLUTION_MAX_HOURS = 12  # Máximo de horas para evolução
EVOLUTION_MIN_HOURS_COOLDOWN = 5  # Tempo mínimo entre evoluções
EVOLUTION_MIN_TRADES = 200  # Mínimo de trades para evolução
EVOLUTION_MIN_RESOLVED_TRADES = 200  # Mínimo de trades resolvidos para evolução (padrão recomendado)
MUTATION_RATE = 0.10
NUM_BOTS = 5
SURVIVORS_PER_CYCLE = 2

# Execution Cost Model (used for edge filtering; conservative defaults)
PAPER_ENTRY_PRICE_BUFFER = _env_float("BOT_ARENA_PAPER_ENTRY_PRICE_BUFFER", 0.010)
LIVE_ENTRY_PRICE_BUFFER = _env_float("BOT_ARENA_LIVE_ENTRY_PRICE_BUFFER", 0.006)
PAPER_FEE_RATE = _env_float("BOT_ARENA_PAPER_FEE_RATE", 0.000)
LIVE_FEE_RATE = _env_float("BOT_ARENA_LIVE_FEE_RATE", 0.000)
MIN_EXPECTED_VALUE = _env_float("BOT_ARENA_MIN_EXPECTED_VALUE", 0.015)
SKIP_RETRY_SECONDS = _env_int("BOT_ARENA_SKIP_RETRY_SECONDS", 45)

# Market timing window - SWEET SPOT: 1 hour to 3 days
# Prioritize markets with enough time for RSI to play out, but not too long
MARKET_FILTER = {
    "min_window_seconds": _env_int("BOT_ARENA_MIN_WINDOW_SECONDS", 3600),        # 1 hour
    "max_window_seconds": _env_int("BOT_ARENA_MAX_WINDOW_SECONDS", 259200),      # 3 days
    "preferred_window": "1h-3d",
    "allow_fallback": True,
    "fallback_min_seconds": _env_int("BOT_ARENA_FALLBACK_MIN_SECONDS", 1800),    # 30 min fallback
    "min_liquidity_usd": _env_float("BOT_ARENA_MIN_LIQUIDITY_USD", 8000.0),      # v3: 8000
    "max_spread_percent": _env_float("BOT_ARENA_MAX_SPREAD_PERCENT", 6.0),       # v3: 6.0% (Rejeita > 6%)
}

# Legacy variables kept for compatibility but redirected to new filter logic where possible
TRADE_MIN_TTE_SECONDS = MARKET_FILTER["min_window_seconds"]
TRADE_MAX_TTE_SECONDS = MARKET_FILTER["max_window_seconds"]

# Risk Management - Position Sizing
POSITION_SIZE_PCT = _env_float("BOT_ARENA_POSITION_SIZE_PCT", 0.01) # 1.0% default
MAX_OPEN_TRADES = _env_int("BOT_ARENA_MAX_OPEN_TRADES", 6)

# Risk Management - SL/TP Defaults (New System)
MEANREV_SL_PCT = _env_float("BOT_ARENA_MEANREV_SL_PCT", -0.25)
MEANREV_TP_PCT = _env_float("BOT_ARENA_MEANREV_TP_PCT", 0.18)
GRACE_PERIOD_SECONDS = _env_int("BOT_ARENA_GRACE_PERIOD_SECONDS", 45)

# Per-Bot SL/TP Enable/Disable
ENABLE_SL_TP_PER_BOT = {
    "meanrev-v1": True,
    "meanrev-sl-v1": True,
    "meanrev-tp-v1": True,
    "hybrid-v1": True,
    "momentum-v1": False,
    "updown-rsi-v3": False,
    "sentiment-v1": False,
    "orderflow-v1": False
}

# Risk Config for Specific Bots (UpDown v3)
RISK_CONFIG = {
    "updown_bot": {
        # Configuração para mercados <= 1 dia (24h)
        "1d": {
            "sl_percent": -48.0,      # Com buffer de spread ~8%
            "tp_partial": 65.0,       # Vende 50%
            "tp_full": 88.0,          # Vende 100%
            "trailing_start": 70.0,   # Começa a trail após +70%
            "trailing_dist": 12.0,    # Distância de 12%
            "monitor_interval": 300,  # 5 min
        },
        # Configuração para mercados <= 3 dias (72h)
        "3d": {
            "sl_percent": -52.0,
            "tp_partial": 58.0,
            "tp_full": 82.0,
            "trailing_start": 65.0,
            "trailing_dist": 12.0,
            "monitor_interval": 900,  # 15 min
        },
        # Configuração Conservadora (> 3 dias)
        "conservative": {
            "sl_percent": -60.0,
            "tp_partial": 50.0,
            "tp_full": 75.0,
            "trailing_start": 60.0,
            "trailing_dist": 15.0,
            "monitor_interval": 1800, # 30 min
        }
    }
}

# Online edge model
MODEL_LR = 0.05
MODEL_L2 = 1e-4

# Signal Feed Settings
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws"
PRICE_UPDATE_INTERVAL_SEC = 1  # Real-time price updates

# Copy Trading Settings
COPYTRADING_ENABLED = True
COPYTRADING_MAX_WALLETS_TO_TRACK = 10
COPYTRADING_POSITION_SIZE_FRACTION = 0.5  # Copy 50% of whale's position size

# Dashboard Settings
DASHBOARD_PORT = 8510
DASHBOARD_HOST = "127.0.0.1"

# Logging
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Sizing and diversity
KELLY_FRACTION = _env_float("BOT_ARENA_KELLY_FRACTION", 0.5)
DIVERSITY_PENALTY = _env_float("BOT_ARENA_DIVERSITY_PENALTY", 0.15)


def get_current_mode():
    """Get current trading mode"""
    return TRADING_MODE


def get_max_position():
    """Get max position size based on current mode"""
    return LIVE_MAX_POSITION if TRADING_MODE == "live" else PAPER_MAX_POSITION


def get_max_daily_loss_per_bot():
    """Get max daily loss per bot based on current mode"""
    return LIVE_MAX_DAILY_LOSS_PER_BOT if TRADING_MODE == "live" else PAPER_MAX_DAILY_LOSS_PER_BOT


def get_max_daily_loss_total():
    """Get max total daily loss based on current mode"""
    return LIVE_MAX_DAILY_LOSS_TOTAL if TRADING_MODE == "live" else PAPER_MAX_DAILY_LOSS_TOTAL


def get_venue():
    """Get trading venue based on current mode"""
    return "polymarket" if TRADING_MODE == "live" else "simmer"

def get_entry_price_buffer():
    return LIVE_ENTRY_PRICE_BUFFER if TRADING_MODE == "live" else PAPER_ENTRY_PRICE_BUFFER


def get_fee_rate():
    return LIVE_FEE_RATE if TRADING_MODE == "live" else PAPER_FEE_RATE


def set_trading_mode(mode: str):
    """
    Set trading mode (paper or live)
    NOTE: This only updates the runtime config, not the config.py file
    For persistence, use the dashboard or manually edit config.py
    """
    global TRADING_MODE
    if mode not in ["paper", "live"]:
        raise ValueError("Mode must be 'paper' or 'live'")
    TRADING_MODE = mode
    return TRADING_MODE


def get_total_position_limit():
    """Get total position limit as percentage of balance (50%)"""
    return MAX_TOTAL_POSITION_PCT_OF_BALANCE


def get_dynamic_max_loss_per_bot(bot_name, mode=None):
    """Get dynamic max loss per bot based on current capital (5% of current capital)"""
    import db
    if mode is None:
        mode = TRADING_MODE
    current_capital = db.get_bot_current_capital(bot_name, mode)
    return current_capital * MAX_LOSS_PCT_PER_BOT


def get_dynamic_max_loss_total(mode=None):
    """Get dynamic max total loss based on current total capital (15% of total capital)"""
    import db
    if mode is None:
        mode = TRADING_MODE
    total_capital = db.get_total_current_capital(mode)
    return total_capital * MAX_LOSS_PCT_TOTAL


def get_min_trade_amount():
    """Get minimum trade amount"""
    return MIN_TRADE_AMOUNT


# Telegram Configuration
TELEGRAM_BOT_TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN", "") or "").strip()  # Get from BotFather
TELEGRAM_CHAT_ID = (os.environ.get("TELEGRAM_CHAT_ID", "") or "").strip()      # Your chat ID
TELEGRAM_ENABLED = os.environ.get("TELEGRAM_ENABLED", "true").lower() == "true"
