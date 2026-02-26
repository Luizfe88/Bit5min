"""Abstract base class all arena bots inherit from."""

import json
import random
import copy
import math
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from logging_config import setup_logging_with_brt
import db
import learning
import edge_model
from telegram_notifier import get_telegram_notifier
from core.risk_manager import risk_manager

logger = setup_logging_with_brt(__name__)


class BaseBot(ABC):
    name: str
    strategy_type: str
    strategy_params: dict
    generation: int
    lineage: str

    # Exit strategy: None = hold to resolution (default)
    # "stop_loss" = exit when position is down stop_loss_pct
    # "take_profit" = exit when position is up take_profit_pct
    exit_strategy: str = None
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0

    # Each strategy type gets different parameters for differentiation.
    # This creates real competition for evolution to select from.
    STRATEGY_PRIORS = {
        "momentum": 0.52,       # slight YES bias — momentum tends bullish
        "mean_reversion": 0.48, # slight NO bias — mean reversion bets against crowd
        "mean_reversion_sl": 0.48,
        "mean_reversion_tp": 0.48,
        "sentiment": 0.50,      # neutral
        "hybrid": 0.50,         # neutral
        "orderflow": 0.50,
        "updown": 0.52,         # momentum bias for short-term
    }
    # How aggressively each strategy trusts the market price signal
    MARKET_PRICE_AGGRESSION = {
        "momentum": 1.2,        # follows market price strongly
        "mean_reversion": 0.95, # nearly follows market (contrarian was -$16 loser)
        "mean_reversion_sl": 0.95,
        "mean_reversion_tp": 0.95,
        "sentiment": 1.0,       # neutral
        "hybrid": 1.0,          # neutral (was 0.9, contrarian loses)
        "orderflow": 1.0,
        "updown": 1.2,          # aggressive
    }
    # Minimum confidence to place a trade (low = trades more, generates learning data)
    MIN_TRADE_CONFIDENCE = {
        "momentum": 0.01,       # trades almost everything (aggressive learner)
        "mean_reversion": 0.06, # slightly selective
        "mean_reversion_sl": 0.06,
        "mean_reversion_tp": 0.06,
        "sentiment": 0.03,      # moderate
        "hybrid": 0.05,         # moderate-selective
        "orderflow": 0.05,
        "updown": 0.01,         # high frequency attempt
    }

    def __init__(self, name, strategy_type, params, generation=0, lineage=None):
        self.name = name
        self.strategy_type = strategy_type
        self.strategy_params = params
        self.generation = generation
        self.lineage = lineage or name
        self._paused = False
        self._pause_reason = None

    @abstractmethod
    def analyze(self, market: dict, signals: dict) -> dict:
        """Analyze market + signals and return a trade signal.

        Returns:
            {
                "action": "buy" | "sell" | "hold",
                "side": "yes" | "no",
                "confidence": 0.0-1.0,
                "reasoning": "why this trade",
                "suggested_amount": float,
            }
        """
        pass

    def make_decision(self, market: dict, signals: dict) -> dict:
        market_price = market.get("current_price", 0.5)
        try:
            market_price = float(market_price)
        except (TypeError, ValueError):
            market_price = 0.5
        market_price = max(0.01, min(0.99, market_price))

        prices = signals.get("prices", []) or []
        btc_latest = signals.get("latest", 0) or 0

        price_momentum = 0.0
        if len(prices) >= 2 and prices[-1] > 0:
            price_momentum = (prices[-1] - prices[-2]) / prices[-2]
        elif btc_latest > 0 and len(prices) >= 1 and prices[-1] > 0:
            price_momentum = (btc_latest - prices[-1]) / prices[-1]

        momentum_signal = max(-0.20, min(0.20, float(price_momentum) * 35))

        vol = 0.0
        if len(prices) >= 6:
            rets = []
            for i in range(max(1, len(prices) - 16), len(prices)):
                p0 = prices[i - 1]
                p1 = prices[i]
                if p0 and p0 > 0:
                    rets.append((p1 - p0) / p0)
            if len(rets) >= 5:
                m = sum(rets) / len(rets)
                var = sum((r - m) ** 2 for r in rets) / max(1, (len(rets) - 1))
                vol = math.sqrt(max(0.0, var))

        raw_signal = self.analyze(market, signals)
        strat = 0.0
        if raw_signal.get("action") != "hold":
            side = raw_signal.get("side")
            conf = raw_signal.get("confidence", 0.0) or 0.0
            strat = (1.0 if side == "yes" else -1.0) * float(conf)

        s = signals.get("sentiment") or {}
        sent = float(s.get("score", 0.5) or 0.5) - 0.5

        of = signals.get("orderflow") or {}
        of_prob = of.get("current_probability", market_price)
        try:
            of_prob = float(of_prob)
        except (TypeError, ValueError):
            of_prob = market_price
        of_delta = max(-0.25, min(0.25, of_prob - market_price))

        of_vol_24h = of.get("volume_24h", 0) or 0
        try:
            of_vol_24h = float(of_vol_24h)
        except (TypeError, ValueError):
            of_vol_24h = 0.0
        of_vol = math.log1p(max(0.0, of_vol_24h)) / 10.0

        tte = of.get("time_to_resolution", 0) or 0
        try:
            tte = float(tte)
        except (TypeError, ValueError):
            tte = 0.0
        tte = max(0.0, min(900.0, tte))
        tte_n = tte / 300.0

        stale = 1.0 if signals.get("stale") else 0.0

        x = {
            "mom": momentum_signal,
            "vol": vol,
            "tte": tte_n,
            "strat": strat,
            "sent": sent,
            "of_delta": of_delta,
            "of_vol": of_vol,
            "stale": stale,
        }

        p_yes = edge_model.predict_yes_probability(self.name, market_price, x)

        entry_buffer = config.get_entry_price_buffer()
        fee_rate = config.get_fee_rate()

        p_buy_yes = max(0.01, min(0.99, market_price + entry_buffer))
        p_buy_no = max(0.01, min(0.99, (1.0 - market_price) + entry_buffer))
        p_eff_yes = max(0.01, min(0.99, p_buy_yes * (1.0 + fee_rate)))
        p_eff_no = max(0.01, min(0.99, p_buy_no * (1.0 + fee_rate)))

        ev_yes = (p_yes - p_eff_yes) / p_eff_yes
        ev_no = ((1.0 - p_yes) - p_eff_no) / p_eff_no

        side = "yes" if ev_yes >= ev_no else "no"
        best_ev = ev_yes if side == "yes" else ev_no

        min_ev = getattr(config, "MIN_EXPECTED_VALUE", 0.0)
        if best_ev < float(min_ev):
            return {
                "action": "skip",
                "side": side,
                "confidence": min(0.95, abs(p_yes - market_price) * 2.5),
                "reasoning": f"No edge after costs: p_yes={p_yes:.3f} mkt={market_price:.3f} ev_yes={ev_yes:.2%} ev_no={ev_no:.2%}",
                "suggested_amount": 0,
                "features": {"x": x, "market_price": market_price, "p_yes": p_yes, "p_entry_yes": p_eff_yes, "p_entry_no": p_eff_no},
            }

        max_pos = config.get_max_position()
        k_frac = getattr(config, "KELLY_FRACTION", 0.5)
        k_yes = (p_yes - p_eff_yes) / max(1e-6, (1.0 - p_eff_yes))
        k_no = ((1.0 - p_yes) - p_eff_no) / max(1e-6, (1.0 - p_eff_no))
        k = k_yes if side == "yes" else k_no
        k = max(0.0, min(0.25, k))
        amount = max_pos * k * k_frac

        confidence = min(0.95, abs(p_yes - market_price) * 2.5)
        reasoning = (
            f"p_yes={p_yes:.3f} mkt={market_price:.3f} "
            f"ev_yes={ev_yes:.2%} ev_no={ev_no:.2%} "
            f"mom={momentum_signal:+.3f} vol={vol:.4f} tte={tte:.0f}s strat={strat:+.3f}"
        )

        return {
            "action": "buy",
            "side": side,
            "confidence": confidence,
            "reasoning": reasoning,
            "suggested_amount": float(amount),
            "features": {"x": x, "market_price": market_price, "p_yes": p_yes, "p_entry_yes": p_eff_yes, "p_entry_no": p_eff_no},
        }

    def execute(self, signal: dict, market: dict) -> dict:
        """Place a trade via Simmer SDK based on the signal."""
        mode = config.get_current_mode()
        try:
            reset_key = f"unpause:{self.name}:{mode}"
            if str(db.get_arena_state(reset_key, "0")) == "1":
                self._paused = False
                self._pause_reason = None
                db.set_arena_state(reset_key, "0")
        except Exception:
            pass

        if self._paused:
            reason_msg = f" ({self._pause_reason})" if self._pause_reason else ""
            logger.info(f"[{self.name}] Paused{reason_msg}, skipping trade")
            return {"success": False, "reason": "bot_paused"}
        venue = config.get_venue()
        max_pos = config.get_max_position()


        max_trades_hr = getattr(config, "MAX_TRADES_PER_HOUR_PER_BOT", None)
        if max_trades_hr is not None:
            try:
                with db.get_conn() as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) as c FROM trades WHERE bot_name=? AND mode=? AND created_at >= datetime('now', '-1 hour')",
                        (self.name, mode),
                    ).fetchone()
                if row and int(dict(row)["c"]) >= int(max_trades_hr):
                    return {"success": False, "reason": "trade_rate_limit"}
            except Exception:
                pass

        # Check risk limits - NOVO SISTEMA CENTRALIZADO
        amount = min(signal.get("suggested_amount", max_pos * 0.5), max_pos)
        
        # Usar o RiskManager centralizado
        allowed, reason = risk_manager.can_place_trade(
            bot_name=self.name,
            amount=amount,
            market=market
        )
        
        if not allowed:
            # Se for daily_loss_per_bot, pausar o bot
            if reason == "daily_loss_per_bot":
                self._paused = True
                self._pause_reason = "daily_loss_limit"
            return {"success": False, "reason": reason}

        try:
            if mode == "live":
                return self._execute_live(signal, market, amount, mode)
            else:
                return self._execute_paper(signal, market, amount, venue, mode)

        except Exception as e:
            logger.error(f"[{self.name}] Trade exception: {e}")
            # Send Telegram notification for trade error
            telegram = get_telegram_notifier()
            if telegram:
                telegram.notify_error(self.name, f"Trade exception: {str(e)}")
            return {"success": False, "reason": str(e)}

    def get_performance(self, hours=12) -> dict:
        """Get bot performance stats."""
        perf = db.get_bot_performance(self.name, hours)
        perf["name"] = self.name
        perf["strategy_type"] = self.strategy_type
        perf["generation"] = self.generation
        perf["paused"] = self._paused
        return perf

    def export_params(self) -> dict:
        return {
            "name": self.name,
            "strategy_type": self.strategy_type,
            "generation": self.generation,
            "lineage": self.lineage,
            "params": copy.deepcopy(self.strategy_params),
        }

    def mutate(self, winning_params: dict, mutation_rate: float = None) -> dict:
        """Create mutated params from winning bot's params."""
        rate = mutation_rate or config.MUTATION_RATE
        new_params = copy.deepcopy(winning_params)

        numeric_keys = [k for k, v in new_params.items() if isinstance(v, (int, float))]
        num_mutations = min(random.randint(2, 3), len(numeric_keys))
        keys_to_mutate = random.sample(numeric_keys, num_mutations) if numeric_keys else []

        for key in keys_to_mutate:
            val = new_params[key]
            delta = val * random.uniform(-rate, rate)
            new_val = val + delta
            if isinstance(val, int):
                new_params[key] = max(1, int(new_val))
            else:
                new_params[key] = max(0.01, round(new_val, 4))

        return new_params

    def reset_daily(self):
        """Reset daily pause state."""
        was_paused = self._paused  # Check if bot was paused before
        self._paused = False
        self._pause_reason = None
        
        # Send Telegram notification if bot was resumed
        if was_paused:
            telegram = get_telegram_notifier()
            if telegram:
                telegram.notify_bot_resumed(self.name)

    def _execute_paper(self, signal, market, amount, venue, mode):
        """Execute via Simmer (paper trading)."""
        import requests
        api_key = self._load_api_key()
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        payload = {
            "market_id": market.get("id") or market.get("market_id"),
            "side": signal["side"],
            "amount": amount,
            "venue": venue,
            "source": f"arena:{self.name}",
            "reasoning": signal.get("reasoning", ""),
        }

        resp = requests.post(
            f"{config.SIMMER_BASE_URL}/api/sdk/trade",
            headers=headers, json=payload, timeout=30
        )

        if resp.status_code in (200, 201):
            result = resp.json()
            db.log_trade(
                bot_name=self.name,
                market_id=market.get("id") or market.get("market_id"),
                market_question=market.get("question"),
                side=signal["side"],
                amount=amount,
                venue=venue,
                mode=mode,
                confidence=signal["confidence"],
                reasoning=signal.get("reasoning"),
                trade_id=result.get("trade_id"),
                shares_bought=result.get("shares_bought"),
                trade_features=signal.get("features"),
            )
            amt_s = f"{amount:.4f}" if float(amount) < 0.01 else f"{amount:.2f}"
            logger.info(f"[{self.name}] Paper trade: {signal['side']} ${amt_s} on {market.get('question', '')[:50]}")
            
            return {"success": True, "trade_id": result.get("trade_id")}
        else:
            logger.error(f"[{self.name}] Paper trade failed: {resp.status_code} {resp.text[:200]}")
            return {"success": False, "reason": f"api_error_{resp.status_code}"}

    def _execute_live(self, signal, market, amount, mode):
        """Execute directly on Polymarket CLOB (live trading)."""
        import polymarket_client

        side = signal["side"].lower()
        if side == "yes":
            token_id = market.get("polymarket_token_id")
        else:
            token_id = market.get("polymarket_no_token_id")

        if not token_id:
            logger.error(f"[{self.name}] No token ID for side={side} on {market.get('question', '')[:50]}")
            return {"success": False, "reason": "missing_token_id"}

        result = polymarket_client.place_market_order(
            token_id=token_id,
            side=side,
            amount=amount,
        )

        if result.get("success"):
            db.log_trade(
                bot_name=self.name,
                market_id=market.get("id") or market.get("market_id"),
                market_question=market.get("question"),
                side=signal["side"],
                amount=amount,
                venue="polymarket",
                mode=mode,
                confidence=signal["confidence"],
                reasoning=signal.get("reasoning"),
                trade_id=result.get("order_id"),
                shares_bought=result.get("size"),
            )
            logger.info(f"[{self.name}] LIVE trade: {signal['side']} ${amount} at {result.get('price')} on {market.get('question', '')[:50]}")
            
        else:
            logger.error(f"[{self.name}] LIVE trade failed: {result.get('error')}")
            # Send Telegram notification for failed trade
            telegram = get_telegram_notifier()
            if telegram:
                telegram.notify_error(self.name, f"Trade failed: {result.get('error', 'Unknown error')}")

        return result

    def _load_api_key(self):
        import json as _json
        # Try per-bot key first, then fall back to default
        try:
            with open(config.SIMMER_BOT_KEYS_PATH) as f:
                bot_keys = _json.load(f)
            if self.name in bot_keys:
                return bot_keys[self.name]
            # Check by slot assignment (for evolved bots inheriting a slot)
            if hasattr(self, '_api_key_slot') and self._api_key_slot in bot_keys:
                return bot_keys[self._api_key_slot]
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        # Fallback: default key
        with open(config.SIMMER_API_KEY_PATH) as f:
            return _json.load(f).get("api_key")
