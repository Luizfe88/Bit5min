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

# New: Trading aggression level (conservative | medium | aggressive)
# Can be set in .env as TRADING_AGGRESSION
TRADING_AGGRESSION = (os.environ.get("TRADING_AGGRESSION") or "medium").lower()

# Aggression multipliers (global multiplier applied to some sizing/thresholds)
AGGRESSION_MULTIPLIERS = {
    "conservative": 0.6,
    "medium": 1.0,
    "aggressive": 1.45,
}

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
DB_PATH = (
    Path(_db_env).expanduser() if _db_env else (Path(__file__).parent / "bot_arena.db")
)

# Target Markets: Price-action markets across multiple liquid assets
TARGET_MARKET_QUERIES = [
    "btc", "bitcoin",
    "eth", "ethereum",
    "sol", "solana",
    "xrp", "ripple",
    "doge", "dogecoin",
    "cpi",
    "interest rate",
    "fed rate",
    "inflation",
]  # Search terms for market discovery

# Keyword triggers: if ANY of these is in the question, consider it a candidate
TARGET_MARKET_KEYWORDS = [
    "5 min", "5-min", "5min",
    "up or down", "up/down",
    "above", "below",           # e.g. "Will ETH be above $X"
    "price", "reach",           # e.g. "Will BTC reach $X"
    "interest rate", "cpi",     # macro price-action markets
]

# Subjective markets to EXCLUDE (politics, sports, culture, elections)
# If ANY of these terms appear in the question, the market is skipped.
EXCLUDED_MARKET_KEYWORDS = [
    # Politics / Elections
    "election", "elect", "president", "trump", "biden", "harris",
    "congress", "senate", "vote", "democrat", "republican", "gop",
    "prime minister", "chancellor", "parliament", "referendum",
    "governor", "mayor", "impeach", "cabinet",
    # Sports
    "nfl", "nba", "mlb", "nhl", "fifa", "champions league",
    "super bowl", "world cup", "olympics", "playoff", "stanley cup",
    "mvp", "championship", "match", "tournament", "league",
    "team", "player", "coach", "score",
    # Culture / Entertainment / Awards
    "oscar", "grammy", "emmy", "golden globe", "academy award",
    "box office", "album", "movie", "film", "song", "artist",
    "celebrity", "kardashian", "taylor swift",
    # Geopolitics
    "war", "ukraine", "russia", "israel", "hamas", "gaza",
    "nato", "sanction",
]

TARGET_MARKET_NAMES = [
    "Bitcoin Up or Down",
    "Ethereum Up or Down",
    "Solana Up or Down",
    "Ripple Up or Down",
    "XRP Up or Down",
    "Dogecoin Up or Down",
    "DOGE Up or Down",
]  # Alternative market names to search
BTC_5MIN_MARKET_ID = None  # Will be populated by setup.py
ETH_5MIN_MARKET_ID = None  # Ethereum market ID
SOL_5MIN_MARKET_ID = None  # Solana market ID
XRP_5MIN_MARKET_ID = None  # XRP market ID
DOGE_5MIN_MARKET_ID = None  # Dogecoin market ID

# Risk Limits - Paper Mode (adjusted for $10000 bankroll)
PAPER_MAX_POSITION = _env_float(
    "BOT_ARENA_PAPER_MAX_POSITION", 50.0
)  # Reduced to $50 to avoid slippage on short markets
PAPER_MAX_DAILY_LOSS_PER_BOT = _env_float(
    "BOT_ARENA_PAPER_MAX_DAILY_LOSS_PER_BOT", 1500.0
)  # 15% of $10k — pausa bot individual
PAPER_MAX_DAILY_LOSS_TOTAL = _env_float(
    "BOT_ARENA_PAPER_MAX_DAILY_LOSS_TOTAL", 5000.0
)  # 50% of $10k — para TUDO
PAPER_STARTING_BALANCE = _env_float(
    "BOT_ARENA_PAPER_STARTING_BALANCE", 10000.0
)  # $10k default bankroll

# Risk Limits - Live Mode (stricter - proportional to $10k bankroll)
LIVE_MAX_POSITION = _env_float("BOT_ARENA_LIVE_MAX_POSITION", 10.0)
LIVE_MAX_DAILY_LOSS_PER_BOT = _env_float(
    "BOT_ARENA_LIVE_MAX_DAILY_LOSS_PER_BOT", 1500.0
)  # 15% of $10k — pausa bot individual
LIVE_MAX_DAILY_LOSS_TOTAL = _env_float(
    "BOT_ARENA_LIVE_MAX_DAILY_LOSS_TOTAL", 5000.0
)  # 50% of $10k — para TUDO

# Dynamic Loss Limits (baseado no capital total — pausa por bot e parada global)
# REGRA: bot pausa com 15% de perda do capital total; TODOS param com 50% global
MAX_LOSS_PCT_PER_BOT = 0.15  # 15% do capital total → pausa o bot individual
MAX_LOSS_PCT_TOTAL = 0.50   # 50% do capital total → para TODOS os bots

# General Risk Rules (both modes)
MAX_POSITION_PCT_OF_BALANCE = 0.02  # Never bet more than 2% of balance per trade
MAX_TOTAL_POSITION_PCT_OF_BALANCE = (
    0.50  # Never allocate more than 50% of total balance
)

# ===== DYNAMIC CONFIDENCE-BASED POSITION SIZING (NEW) =====
# Controls how position size scales with confidence level
BASE_POSITION_PERCENT = 0.08  # 8% of total capital as base per trade
MAX_TOTAL_EXPOSURE = 0.50  # 50% of total capital max in open positions
MIN_TRADE_SIZE = 5.0  # Minimum position size in dollars ($5)

# Confidence Tiers: Maps confidence ranges to position multipliers
# Higher confidence = more aggressive position sizing
CONFIDENCE_POSITION_MULTIPLIERS = {
    # confidence >= X: multiplier
    0.90: 2.8,  # Ultra aggressive: 8% * 2.8 = 22.4% per position
    0.80: 2.2,  # Very aggressive: 8% * 2.2 = 17.6%
    0.70: 1.65,  # Aggressive: 8% * 1.65 = 13.2%
    0.60: 1.15,  # Medium: 8% * 1.15 = 9.2%
    0.50: 0.65,  # Conservative: 8% * 0.65 = 5.2%
    0.00: 0.35,  # Very conservative: 8% * 0.35 = 2.8%
}

MAX_CONSECUTIVE_LOSSES = _env_int(
    "BOT_ARENA_MAX_CONSECUTIVE_LOSSES", 3
)  # Pause after 3 consecutive losses
PAUSE_AFTER_CONSECUTIVE_LOSSES_SECONDS = _env_int(
    "BOT_ARENA_PAUSE_AFTER_CONSECUTIVE_LOSSES", 3600
)  # Pause for 1 hour
MAX_TRADES_PER_HOUR_PER_BOT = 20  # Hard cap to prevent overtrading in 5-min markets
MIN_TRADE_AMOUNT = _env_float(
    "BOT_ARENA_MIN_TRADE_AMOUNT", 0.01
)  # Minimum trade amount

# Evolution Settings
EVOLUTION_INTERVAL_HOURS = 12  # Safety net: máximo 12h sem evolução
EVOLUTION_MAX_HOURS = 12  # Máximo de horas para evolução
EVOLUTION_MIN_HOURS_COOLDOWN = 5  # Tempo mínimo entre evoluções
EVOLUTION_MIN_TRADES = 200  # Mínimo de trades para evolução
EVOLUTION_MIN_RESOLVED_TRADES = (
    200  # Mínimo de trades resolvidos para evolução (padrão recomendado)
)
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

# Dynamic thresholds per aggression level (percent as fraction)
AGGRESSION_THRESHOLDS = {
    "conservative": {
        "min_edge_after_fees": 0.0080,  # 0.80%
        "min_confidence": 0.72,
        "max_spread_allowed": 0.9,  # percent
        "max_trades_per_hour": _env_int("BOT_ARENA_MAX_TRADES_PER_HOUR_PER_BOT", 20),
    },
    "medium": {
        "min_edge_after_fees": 0.0035,  # 0.35%
        "min_confidence": 0.60,
        "max_spread_allowed": 1.4,  # percent
        "max_trades_per_hour": _env_int("BOT_ARENA_MAX_TRADES_PER_HOUR_PER_BOT", 20),
    },
    "aggressive": {
        "min_edge_after_fees": -0.0300,  # ACEITA EV NEGATIVO (-3%) PARA COLETAR DADOS (COLD START)
        "min_confidence": 0.48,  # MODO AGGRESSIVE: conforme pedido do usuário (aceita perdas por lucros maiores)
        "max_spread_allowed": 1.5,  # percent
        # Hard cap in aggressive mode: prevent flood
        "max_trades_per_hour": 12,
    },
}

# Market timing window - SWEET SPOT: 1 hour to 3 days
# Prioritize markets with enough time for RSI to play out, but not too long
MARKET_FILTER = {
    "min_window_seconds": _env_int("BOT_ARENA_MIN_WINDOW_SECONDS", 3600),  # 1 hour
    "max_window_seconds": _env_int("BOT_ARENA_MAX_WINDOW_SECONDS", 259200),  # 3 days
    "preferred_window": "1h-3d",
    "allow_fallback": True,
    "fallback_min_seconds": _env_int(
        "BOT_ARENA_FALLBACK_MIN_SECONDS", 1800
    ),  # 30 min fallback
    "min_liquidity_usd": _env_float(
        "BOT_ARENA_MIN_LIQUIDITY_USD", 8000.0
    ),  # Strict 8000.0 limit
    "max_spread_percent": _env_float(
        "BOT_ARENA_MAX_SPREAD_PERCENT", 6.0
    ),  # v3: 6.0% (Rejeita > 6%)
}

# Legacy variables kept for compatibility but redirected to new filter logic where possible
TRADE_MIN_TTE_SECONDS = MARKET_FILTER["min_window_seconds"]
TRADE_MAX_TTE_SECONDS = MARKET_FILTER["max_window_seconds"]

# Risk Management - Position Sizing
POSITION_SIZE_PCT = _env_float("BOT_ARENA_POSITION_SIZE_PCT", 0.01)  # 1.0% default
MAX_OPEN_TRADES = _env_int("BOT_ARENA_MAX_OPEN_TRADES", 6)
MAX_BOTS_PER_MARKET = _env_int("BOT_ARENA_MAX_BOTS_PER_MARKET", 2)

# Risk Management - SL/TP Defaults (New System)
MEANREV_SL_PCT = _env_float("BOT_ARENA_MEANREV_SL_PCT", -0.25)
MEANREV_TP_PCT = _env_float("BOT_ARENA_MEANREV_TP_PCT", 0.18)
GRACE_PERIOD_SECONDS = _env_int("BOT_ARENA_GRACE_PERIOD_SECONDS", 45)

# Per-Bot SL/TP Enable/Disable
# This matches substrings, so "mean_reversion" matches "mean_reversion-g4-179"
ENABLE_SL_TP_PER_BOT = {
    "meanrev-v1": True,
    "meanrev-sl-v1": True,
    "meanrev-tp-v1": True,
    "mean_reversion": True,  # Ensures evolved bots get SL/TP
    "hybrid": True,
    "momentum": True,
    "updown": True,
    "sentiment": True,
    "orderflow": True,
}

# Risk Config for Specific Bots (UpDown v3)
RISK_CONFIG = {
    "updown_bot": {
        # Configuração para mercados <= 1 dia (24h)
        "1d": {
            "sl_percent": -48.0,  # Com buffer de spread ~8%
            "tp_partial": 65.0,  # Vende 50%
            "tp_full": 88.0,  # Vende 100%
            "trailing_start": 70.0,  # Começa a trail após +70%
            "trailing_dist": 12.0,  # Distância de 12%
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
            "monitor_interval": 1800,  # 30 min
        },
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
COPY_MIN_PRICE = 0.10  # Não copia trades se o alvo pagou menos de 10 centavos
COPY_MAX_PRICE = 0.85  # Não copia trades se o alvo pagou mais de 85 centavos

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
    return (
        LIVE_MAX_DAILY_LOSS_PER_BOT
        if TRADING_MODE == "live"
        else PAPER_MAX_DAILY_LOSS_PER_BOT
    )


def get_max_daily_loss_total():
    """Get max total daily loss based on current mode"""
    return (
        LIVE_MAX_DAILY_LOSS_TOTAL
        if TRADING_MODE == "live"
        else PAPER_MAX_DAILY_LOSS_TOTAL
    )


def get_venue():
    """Get trading venue based on current mode"""
    return "polymarket" if TRADING_MODE == "live" else "simmer"


def get_entry_price_buffer():
    return (
        LIVE_ENTRY_PRICE_BUFFER if TRADING_MODE == "live" else PAPER_ENTRY_PRICE_BUFFER
    )


def get_fee_rate():
    return LIVE_FEE_RATE if TRADING_MODE == "live" else PAPER_FEE_RATE


def get_aggression_level() -> str:
    """Return configured aggression level: conservative|medium|aggressive"""
    a = (os.environ.get("TRADING_AGGRESSION") or TRADING_AGGRESSION or "medium").lower()

    # Tenta ler do banco de dados (persistência do Telegram via /mode)
    try:
        import sqlite3
        with sqlite3.connect(DB_PATH, timeout=1.0) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM arena_state WHERE key = 'trading_aggression'")
            result = cursor.fetchone()
            if result and result[0] in AGGRESSION_MULTIPLIERS:
                a = result[0].lower()
    except Exception:
        pass

    if a not in AGGRESSION_MULTIPLIERS:
        return "medium"
    return a


def get_aggression_multiplier() -> float:
    return AGGRESSION_MULTIPLIERS.get(get_aggression_level(), 1.0)


def get_min_edge_after_fees() -> float:
    return AGGRESSION_THRESHOLDS.get(
        get_aggression_level(), AGGRESSION_THRESHOLDS["medium"]
    )["min_edge_after_fees"]


def get_min_confidence() -> float:
    return AGGRESSION_THRESHOLDS.get(
        get_aggression_level(), AGGRESSION_THRESHOLDS["medium"]
    )["min_confidence"]


def get_max_spread_allowed() -> float:
    """Return spread percent allowed (e.g. 1.4 means 1.4%)."""
    return AGGRESSION_THRESHOLDS.get(
        get_aggression_level(), AGGRESSION_THRESHOLDS["medium"]
    )["max_spread_allowed"]


def get_max_trades_per_hour_per_bot() -> int:
    # In aggressive mode we will enforce a hard cap of 8 trades/hr as safety
    lvl = get_aggression_level()
    if lvl == "aggressive":
        return min(8, AGGRESSION_THRESHOLDS[lvl]["max_trades_per_hour"] or 8)
    return AGGRESSION_THRESHOLDS.get(lvl, AGGRESSION_THRESHOLDS["medium"])[
        "max_trades_per_hour"
    ]


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


# ===== DYNAMIC CONFIDENCE-BASED POSITION SIZING GETTERS =====
def get_confidence_multiplier(confidence: float) -> float:
    """Get position multiplier based on confidence level.

    Args:
        confidence: Float between 0.0 and 1.0

    Returns:
        Multiplier to apply to base position size
    """
    # Find the highest tier that confidence meets or exceeds
    for tier_threshold in sorted(CONFIDENCE_POSITION_MULTIPLIERS.keys(), reverse=True):
        if confidence >= tier_threshold:
            return CONFIDENCE_POSITION_MULTIPLIERS[tier_threshold]

    # Fallback (shouldn't reach here)
    return CONFIDENCE_POSITION_MULTIPLIERS[0.00]


def get_base_position_percent() -> float:
    """Get base position size as percentage of total capital (8%)"""
    return BASE_POSITION_PERCENT


def get_max_total_exposure() -> float:
    """Get maximum total exposure as percentage of capital (50%)"""
    return MAX_TOTAL_EXPOSURE


def get_min_trade_size() -> float:
    """Get minimum trade size in dollars ($5)"""
    return MIN_TRADE_SIZE


# Telegram Configuration
TELEGRAM_BOT_TOKEN = (
    os.environ.get("TELEGRAM_BOT_TOKEN", "") or ""
).strip()  # Get from BotFather
TELEGRAM_CHAT_ID = (
    os.environ.get("TELEGRAM_CHAT_ID", "") or ""
).strip()  # Your chat ID
TELEGRAM_ENABLED = os.environ.get("TELEGRAM_ENABLED", "true").lower() == "true"
