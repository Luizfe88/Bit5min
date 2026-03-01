"""Abstract base class all arena bots inherit from."""
# Atualizado em 2026-02-26 - Validação forte + Retry inteligente + Trade ID robusto + Market Lock

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
from core.market_lock import market_lock
from core.slippage_model import SlippageCalculator

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
        "momentum": 0.52,  # slight YES bias — momentum tends bullish
        "mean_reversion": 0.48,  # slight NO bias — mean reversion bets against crowd
        "mean_reversion_sl": 0.48,
        "mean_reversion_tp": 0.48,
        "sentiment": 0.50,  # neutral
        "hybrid": 0.50,  # neutral
        "orderflow": 0.50,
        "updown": 0.52,  # momentum bias for short-term
    }
    # How aggressively each strategy trusts the market price signal
    MARKET_PRICE_AGGRESSION = {
        "momentum": 1.2,  # follows market price strongly
        "mean_reversion": 0.95,  # nearly follows market (contrarian was -$16 loser)
        "mean_reversion_sl": 0.95,
        "mean_reversion_tp": 0.95,
        "sentiment": 1.0,  # neutral
        "hybrid": 1.0,  # neutral (was 0.9, contrarian loses)
        "orderflow": 1.0,
        "updown": 1.2,  # aggressive
    }
    # Minimum confidence to place a trade (low = trades more, generates learning data)
    MIN_TRADE_CONFIDENCE = {
        "momentum": 0.01,  # trades almost everything (aggressive learner)
        "mean_reversion": 0.06,  # slightly selective
        "mean_reversion_sl": 0.06,
        "mean_reversion_tp": 0.06,
        "sentiment": 0.03,  # moderate
        "hybrid": 0.05,  # moderate-selective
        "orderflow": 0.05,
        "updown": 0.01,  # high frequency attempt
    }

    def __init__(self, name, strategy_type, params, generation=0, lineage=None):
        self.name = name
        self.strategy_type = strategy_type
        self.strategy_params = params
        self.generation = generation
        self.lineage = lineage or name
        self._paused = False
        self._pause_reason = None

        # --- Centralized SL/TP Configuration ---
        # Default disabled, check config.ENABLE_SL_TP_PER_BOT first
        self.enable_sl_tp = False

        # 1. Check Global Config per Bot Name
        # Exact match or substring match for "meanrev" etc
        for key, enabled in config.ENABLE_SL_TP_PER_BOT.items():
            if key in name:
                self.enable_sl_tp = enabled
                break

        # 2. Defaults from Config
        self.sl_pct = config.MEANREV_SL_PCT  # Default -25%
        self.tp_pct = config.MEANREV_TP_PCT  # Default +18%

        # 3. Override if class has specific attributes (e.g. MeanRevSLBot, MeanRevTPBot)
        if hasattr(self, "stop_loss_pct") and self.stop_loss_pct > 0:
            self.sl_pct = -abs(self.stop_loss_pct)
        if hasattr(self, "take_profit_pct") and self.take_profit_pct > 0:
            self.tp_pct = abs(self.take_profit_pct)

        # 4. Override if params has sl_pct or tp_pct
        if params:
            if "sl_pct" in params:
                self.sl_pct = -abs(float(params["sl_pct"]))
            if "tp_pct" in params:
                self.tp_pct = abs(float(params["tp_pct"]))

        # --- Trailing TP Configuration ---
        # Carrega do strategy_params se disponível, senão usa defaults
        self.trailing_enabled = self.strategy_params.get("trailing_enabled", False)
        self.trailing_distance = self.strategy_params.get("trailing_distance", 0.045)
        self.trailing_step = self.strategy_params.get("trailing_step", 0.015)

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

        # Helper: calculate effective edge after fees (transparent helper)
        def calculate_real_edge_after_fees(p_yes, market_price, p_eff_yes, p_eff_no):
            ev_yes = (p_yes - p_eff_yes) / max(1e-9, p_eff_yes)
            ev_no = ((1.0 - p_yes) - p_eff_no) / max(1e-9, p_eff_no)
            side = "yes" if ev_yes >= ev_no else "no"
            best_ev = ev_yes if side == "yes" else ev_no
            return ev_yes, ev_no, best_ev, side

        ev_yes, ev_no, best_ev, side = calculate_real_edge_after_fees(
            p_yes, market_price, p_eff_yes, p_eff_no
        )

        # Dynamic minimum edge based on configured aggression
        min_ev = config.get_min_edge_after_fees()
        # Log & return skip if edge below threshold
        if best_ev < float(min_ev):
            # compute spread percent if available
            spread_pct = None
            try:
                bb = float(market.get("best_bid") or 0)
                ba = float(market.get("best_ask") or 0)
                if bb > 0 and ba > 0:
                    mid = (bb + ba) / 2
                    spread_pct = (ba - bb) / mid * 100
            except Exception:
                spread_pct = None

            reason_text = f"No edge after costs: p_yes={p_yes:.3f} mkt={market_price:.3f} ev_yes={ev_yes:.2%} ev_no={ev_no:.2%}"
            logger.info(
                f"[{self.name}] SKIP: {reason_text} | min_ev={min_ev:.4f} | spread_pct={spread_pct if spread_pct is not None else 'N/A'}"
            )
            return {
                "action": "skip",
                "side": side,
                "confidence": min(0.95, abs(p_yes - market_price) * 2.5),
                "reasoning": reason_text,
                "suggested_amount": 0,
                "features": {
                    "x": x,
                    "market_price": market_price,
                    "p_yes": p_yes,
                    "p_entry_yes": p_eff_yes,
                    "p_entry_no": p_eff_no,
                },
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

        # Combine ML features with any custom trade features from analyze()
        final_features = {
            "x": x,
            "market_price": market_price,
            "p_yes": p_yes,
            "p_entry_yes": p_eff_yes,
            "p_entry_no": p_eff_no,
        }
        if raw_signal.get("trade_features"):
            final_features.update(raw_signal.get("trade_features"))

        # Dynamic position sizing: boost when very confident
        try:
            conf_float = float(confidence)
        except Exception:
            conf_float = 0.0

        if conf_float >= 0.82:
            # scale factor between 1.5 and 2.0 influenced by aggression level
            agg_lvl = (
                config.get_aggression_level()
                if hasattr(config, "get_aggression_level")
                else "medium"
            )
            if agg_lvl == "aggressive":
                mult = 2.0
            elif agg_lvl == "medium":
                mult = 1.65
            else:
                mult = 1.5
            amount = amount * mult
            reasoning = f"[SIZE x{mult:.2f}] " + reasoning

        return {
            "action": "buy",
            "side": side,
            "confidence": confidence,
            "reasoning": reasoning,
            "suggested_amount": float(amount),
            "features": final_features,
        }

    def _fetch_market_price(self, market_id: str) -> float:
        """Fetch current market price directly from API (Retry Logic)."""
        import requests
        import time

        api_key = self._load_api_key()
        headers = {"Authorization": f"Bearer {api_key}"}

        # FIX: Robust Price Fetching with Backoff
        for attempt in range(3):
            try:
                resp = requests.get(
                    f"{config.SIMMER_BASE_URL}/api/sdk/markets/{market_id}",
                    headers=headers,
                    timeout=5,
                )
                if resp.status_code == 200:
                    m_data = resp.json()
                    # Simmer returns 'current_price' as probability of YES
                    price = float(
                        m_data.get("current_price")
                        or m_data.get("last_price")
                        or m_data.get("mid_price")
                        or 0.0
                    )
                    if 0 < price < 1.1:
                        return price
            except Exception as e:
                logger.warning(
                    f"[{self.name}] Price fetch attempt {attempt + 1} failed: {e}"
                )

            time.sleep(0.3 * (2**attempt))  # Backoff: 0.3, 0.6, 1.2

        # FIX: Abort trade if price fetch fails
        return 0.0

    # FIX: Hard price filter para mean-reversion segura
    def is_valid_entry_price(self, price: float, side: str) -> bool:
        if price < 0.18 or price > 0.82:
            logger.warning(
                f"[PRICE FILTER] Preço {price:.3f} fora do range seguro 0.18-0.82. Abortando."
            )
            return False

        # Bonus: filtro assimétrico mais forte
        if side == "yes" and price > 0.75:
            logger.warning(f"[PRICE FILTER] YES a {price:.3f} muito alto. Abortando.")
            return False
        if side == "no" and price < 0.25:
            logger.warning(f"[PRICE FILTER] NO a {price:.3f} muito baixo. Abortando.")
            return False

        return True

    def calculate_position_size(
        self, confidence: float, current_exposure: float, total_capital: float
    ) -> float:
        """
        Calculate dynamic position size based on confidence level with global exposure cap.

        Higher confidence = more aggressive position sizing
        Global cap prevents over-leveraging across all open positions

        Args:
            confidence: Signal confidence (0.0-1.0), higher = more aggressive
            current_exposure: Current total open position value across all bots ($)
            total_capital: Total available capital ($)

        Returns:
            Position size in dollars (rounded to 2 decimals)
            Min: MIN_TRADE_SIZE ($5)
            Max: 50% of capital - current_exposure

        Logic:
            1. Get confidence tier multiplier (0.90→2.8, 0.80→2.2, etc.)
            2. base_size = 8% * total_capital
            3. desired_size = base_size * multiplier
            4. Respect global 50% cap: max_allowed = 50% * capital - current_exposure
            5. final_size = min(desired_size, max_allowed)
            6. Enforce minimum: final_size = max(final_size, $5)
        """
        # Get confidence-based multiplier
        multiplier = config.get_confidence_multiplier(confidence)

        # Calculate desired position size
        base_size = config.get_base_position_percent() * total_capital
        desired_size = base_size * multiplier

        # Global exposure cap: 50% of capital
        max_global = config.get_max_total_exposure() * total_capital
        max_allowed = max(0.0, max_global - current_exposure)

        # Apply cap
        final_size = min(desired_size, max_allowed)

        # Enforce minimum trade size
        min_size = config.get_min_trade_size()
        final_size = max(final_size, min_size)

        # Round to 2 decimals
        final_size = round(final_size, 2)

        # Calculate total exposure percentage for logging
        total_exposure_pct = (
            (current_exposure + final_size) * 100 / total_capital
            if total_capital > 0
            else 0
        )

        # Log-friendly string with all details
        logger.debug(
            f"[{self.name}] Position Sizing: conf={confidence:.2f} → multiplier={multiplier:.2f} → "
            f"desired=${desired_size:.2f} → final=${final_size:.2f} "
            f"(total_exposure={total_exposure_pct:.1f}% of capital)"
        )

        return final_size

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
        # Trust floor: ignore any signal with confidence lower than configured min
        conf = signal.get("confidence", 0.0) or 0.0
        try:
            conf = float(conf)
        except Exception:
            conf = 0.0
        min_conf = (
            config.get_min_confidence()
            if hasattr(config, "get_min_confidence")
            else 0.55
        )
        if conf < float(min_conf):
            logger.info(
                f"[{self.name}] Signal ignored. Confiança muito baixa ({conf:.2f}) < min_conf={min_conf:.2f}"
            )
            return {"success": False, "reason": "low_confidence"}
        venue = config.get_venue()
        max_pos = config.get_max_position()

        # FIX: Market Lock Check
        m_id = market.get("id") or market.get("market_id")
        if m_id and market_lock.is_locked(m_id):
            logger.info(
                f"[{self.name}] Market {m_id} is locked by other strategy. Skipping."
            )
            return {"success": False, "reason": "market_locked"}

        # FIX: Hard Price Filter
        current_price = market.get("current_price", 0.5)
        try:
            current_price = float(current_price)
        except:
            current_price = 0.5

        side = signal.get("side", "yes")
        if not self.is_valid_entry_price(current_price, side):
            return {"success": False, "reason": "price_filter_abort"}

        max_trades_hr = (
            config.get_max_trades_per_hour_per_bot()
            if hasattr(config, "get_max_trades_per_hour_per_bot")
            else getattr(config, "MAX_TRADES_PER_HOUR_PER_BOT", None)
        )
        if max_trades_hr is not None:
            try:
                with db.get_conn() as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) as c FROM trades WHERE bot_name=? AND mode=? AND created_at >= datetime('now', '-1 hour')",
                        (self.name, mode),
                    ).fetchone()
                if row and int(dict(row)["c"]) >= int(max_trades_hr):
                    logger.info(
                        f"[{self.name}] Hourly trade cap reached ({dict(row)['c']} >= {max_trades_hr}) - enforcing rate limit"
                    )
                    return {"success": False, "reason": "trade_rate_limit"}
            except Exception:
                pass

        # ===== DYNAMIC CONFIDENCE-BASED POSITION SIZING (NEW) =====
        # Get current total exposure and total capital for sizing calculation
        try:
            total_capital = (
                config.PAPER_STARTING_BALANCE
                if mode == "paper"
                else config.LIVE_STARTING_BALANCE
            )
        except:
            total_capital = 10000.0  # Fallback

        try:
            # Get current total exposure across all open positions
            current_exposure = db.get_total_open_position_value_all_bots(mode)
        except:
            current_exposure = 0.0  # Fallback

        # Calculate position size based on confidence level
        amount = self.calculate_position_size(conf, current_exposure, total_capital)

        # If calculated position is below minimum, skip trade
        if amount < config.get_min_trade_size():
            logger.info(
                f"[{self.name}] Position too small (${amount:.2f} < min ${config.get_min_trade_size():.2f}). Skipping."
            )
            return {"success": False, "reason": "position_below_minimum"}

        # Dynamic Size uses its own multiplier based on 50% Bankroll rules, and no longer
        # blindly restricted by max_pos which caused sizing artificial limits on $10k bankrolls.
        # amount = min(amount, max_pos)  <-- REMOVIDO para sizing dinâmico funcionar livremente.

        # Usar o RiskManager centralizado
        allowed, reason = risk_manager.can_place_trade(
            bot_name=self.name, amount=amount, market=market
        )

        if not allowed:
            logger.info(
                f"[{self.name}] RiskManager denied trade: reason={reason} amount={amount} market_id={m_id}"
            )
            # Se for daily_loss_per_bot, pausar o bot
            if reason == "daily_loss_per_bot":
                self._paused = True
                self._pause_reason = "daily_loss_limit"
            return {"success": False, "reason": reason}

        # --- SL/TP Calculation Delegates ---
        # A lógica para SL/TP agora ocorre dinamicamente dentro dos 
        # métodos _execute_live e _execute_paper usando o RiskManager centralizado,
        # após sabermos o real fill_price (Slippage ou Real Price).
        
        try:
            if mode == "live":
                result = self._execute_live(
                    signal, market, amount, mode
                )
            else:
                result = self._execute_paper(
                    signal,
                    market,
                    amount,
                    venue,
                    mode
                )

            # --- Registrar Posição no RiskManager ---
            if result.get("success"):
                # FIX: Acquire Lock
                if m_id:
                    market_lock.acquire_lock(m_id)

                try:
                    from core.position import OpenPosition
                    import time
                    import uuid

                    side = signal.get(
                        "side", "yes"
                    ).lower()  # FIX: Safer access with default
                    token_id = None
                    if side == "yes":
                        token_id = market.get("polymarket_token_id")
                    else:
                        token_id = market.get("polymarket_no_token_id")

                    # 1. Tentar pegar preço explícito do retorno da execução
                    entry_price = float(
                        result.get("price") or result.get("avgPrice") or 0
                    )

                    # 2. Se falhar, tentar calcular via amount / shares
                    shares = float(
                        result.get("shares_bought") or result.get("size") or 0
                    )

                    # FIX: Fallback to calculate shares if size/shares_bought is missing but we have price
                    if shares <= 0 and entry_price > 0 and amount > 0:
                        shares = amount / entry_price
                        logger.warning(
                            f"[{self.name}] Shares missing but price found ({entry_price}). Calculated shares: {shares:.4f}"
                        )

                    # FIX: Rigid Fill Validation
                    if shares <= 0:
                        # Captura o preço atual da tela para análise de spread
                        try:
                            current_price_debug = (
                                market.get("current_price", "N/A")
                                if isinstance(market, dict)
                                else market
                            )
                        except Exception:
                            current_price_debug = "N/A"

                        logger.error(
                            f"[DEBUG SHARES=0] Bot: {getattr(self, 'name', 'Unknown')} | Side: {side} | Amount: ${amount} | Preço Tela: {current_price_debug} | Spread/Slippage consumiu a ordem inteira no mercado {m_id}"
                        )
                        logger.error(
                            f"[{self.name}] Trade executed but shares=0. Response: {json.dumps(result)}"
                        )
                        return {"success": False, "reason": "zero_shares_fill"}

                    if entry_price <= 0 and shares > 0:
                        entry_price = amount / shares

                    # FIX: If calculated price is absurd (> 0.99), assume calculation failed and try fetch
                    if entry_price > 0.99:
                        logger.warning(
                            f"[{self.name}] Calculated price {entry_price:.3f} is invalid (>0.99). Shares: {shares}, Amount: {amount}"
                        )
                        entry_price = 0  # Force fetch

                    # 3. Retry Inteligente: Buscar na API se ainda inválido
                    if entry_price <= 0:
                        logger.info(
                            f"[{self.name}] Entry price missing. Fetching from API..."
                        )
                        p_yes = self._fetch_market_price(m_id)
                        if p_yes > 0:
                            entry_price = p_yes if side == "yes" else (1.0 - p_yes)
                            logger.info(
                                f"[{self.name}] Price recovered from API: {entry_price:.3f}"
                            )
                        else:
                            # FIX: Abort if price fetch fails
                            logger.error(
                                f"[{self.name}] Failed to resolve entry price. Aborting position registration."
                            )
                            return {"success": False, "reason": "price_fetch_failed"}

                    # --- VALIDAÇÃO FORTE DE PREÇO ---
                    if not (0 < entry_price <= 0.99):
                        logger.error(
                            f"[{self.name}] Preço inválido ou fora do range (max 0.99): {entry_price:.3f} - abortando entrada"
                        )
                        return {"success": False, "reason": "invalid_entry_price"}

                    # --- Trade ID Robusto ---
                    trade_id = (
                        result.get("trade_id")
                        or result.get("order_id")
                        or result.get("id")
                    )

                    # Log Debug Completo para entender estrutura do response
                    if not trade_id:
                        logger.warning(
                            f"[DEBUG ORDER RESPONSE] {json.dumps(result, indent=2)}"
                        )

                    if not trade_id:
                        trade_id = f"temp_{uuid.uuid4().hex[:8]}"
                        logger.warning(
                            f"Trade ID ausente no retorno da execução para {self.name}. Usando ID temp: {trade_id}"
                        )

                    # --- SL/TP Calculation (Centralized) ---
                    sl_price = signal.get("sl_price")
                    tp_price = signal.get("tp_price")

                    pos = OpenPosition(
                        market_id=market.get("id") or market.get("market_id"),
                        bot_name=self.name,
                        direction=signal["side"],
                        entry_price=entry_price,
                        size_usd=amount,
                        entry_time=time.time(),
                        sl_price=sl_price,
                        tp_price=tp_price,
                        confidence=signal.get("confidence", 0.0),
                        trade_id=trade_id,
                        shares=shares,
                        token_id=token_id,
                        # Trailing TP Configuration
                        trailing_enabled=getattr(self, "trailing_enabled", False),
                        trailing_distance=getattr(self, "trailing_distance", None),
                        trailing_step=getattr(self, "trailing_step", None),
                    )
                    risk_manager.add_position(pos)
                except Exception as pos_err:
                    logger.error(f"Erro ao registrar posição no RiskManager: {pos_err}")

            return result

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
        keys_to_mutate = (
            random.sample(numeric_keys, num_mutations) if numeric_keys else []
        )

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

    def _execute_paper(
        self, signal, market, amount, venue, mode
    ):
        """Local paper execution: calculate shares and persist locally.

        NOTE: This intentionally avoids placing any orders on Polymarket.
        """
        try:
            market_id = market.get("id") or market.get("market_id")

            # Prefer market-provided price; fallback to API fetcher
            price = None
            try:
                if market and market.get("current_price") is not None:
                    price = float(market.get("current_price"))
            except Exception:
                price = None

            if price is None or price <= 0:
                price = self._fetch_market_price(market_id)

            if price is None or price <= 0:
                logger.error(
                    f"[{self.name}] Unable to determine market price for paper trade on {market_id}"
                )
                return {"success": False, "reason": "price_unavailable"}

            # --- SQAURE ROOT SLIPPAGE IMPACT MODEL ---
            m_vol_24h = market.get("volume_24h", 1000.0)
            try:
                m_vol_24h = float(m_vol_24h)
            except (ValueError, TypeError):
                m_vol_24h = 1000.0

            # O valor do 'price' captado da API é sempre em relação à probabilidade do YES
            # Se a decisão for "no", a probabilidade base a comprar é 1 - price.
            # O SlippageCalculator já cuida disso internamente e devolve o Target + Penalidade.
            fill_price = SlippageCalculator.calculate_fill_price(
                side=signal["side"], 
                order_amount_usd=amount, 
                market_price=price, 
                market_volume_24h=m_vol_24h
            )

            # LIMITES DE LIQUIDEZ E SLIPPAGE EXTREMO
            # Um price limit aceitável em Polymarket costuma bater até 0.99
            if fill_price >= 0.99:
                logger.error(
                    f"[{self.name}] Liquidez Insuficiente. Slippage empurrou preço de paper trade ({signal['side']}) para {fill_price:.3f} >= 0.99. Abortando trade para evitar overfitting."
                )
                return {"success": False, "reason": "insufficient_liquidity"}

            # shares = amount / price (agora usando fill_price penalizado)
            shares = round(float(amount) / float(fill_price), 6) if fill_price > 0 else 0.0

            # --- SL/TP RECALCULATION DUE TO SLIPPAGE (FIX LOGGING BUG) ---
            # O sistema até agora estava enviando sl_price=None ou calculando base no old current_price.
            # O db recebia campos nulls pois recálculo não estava sincronizado com o db persist aqui no paper.
            
            sl_tp_dict = risk_manager.calculate_sl_tp(
                fill_price=fill_price,
                enable_sl_tp=getattr(self, "enable_sl_tp", False),
                sl_pct=getattr(self, "sl_pct", 0.0),
                tp_pct=getattr(self, "tp_pct", 0.0),
                trailing_enabled=getattr(self, "trailing_enabled", False),
                trailing_distance=getattr(self, "trailing_distance", 0.045)
            )
            
            # --- DEFAULT/FALLBACK VALIDATION ---
            # If SL/TP is enabled but calculate_sl_tp returned None, we force a fallback logic
            if getattr(self, "enable_sl_tp", False):
                if sl_tp_dict["sl_price"] is None:
                    sl_tp_dict["sl_price"] = max(0.001, fill_price * 0.90)  # Emergência: 10% abaixo da entrada
                    logger.warning(f"[{self.name}] Emergência SL ativado: {sl_tp_dict['sl_price']:.3f}")
                if sl_tp_dict["tp_price"] is None:
                    sl_tp_dict["tp_price"] = min(0.999, fill_price * 1.15)  # Emergência: 15% acima da entrada

            signal["sl_price"] = sl_tp_dict["sl_price"]
            signal["tp_price"] = sl_tp_dict["tp_price"]
            
            # Persist trade locally in DB (no external order placed)
            import uuid

            trade_id = f"paper_{uuid.uuid4().hex[:8]}"
            db.log_trade(
                bot_name=self.name,
                market_id=market_id,
                market_question=market.get("question"),
                side=signal["side"],
                amount=amount,
                venue="local_paper",
                mode=mode,
                confidence=signal.get("confidence"),
                reasoning=signal.get("reasoning"),
                trade_id=trade_id,
                shares_bought=shares,
                trade_features=signal.get("features"),
                sl_price=signal["sl_price"],   # FIX: Agora persistidos corretamente
                tp_price=signal["tp_price"],   # FIX: Agora persistidos corretamente
            )

            amt_s = f"{amount:.4f}" if float(amount) < 0.01 else f"{amount:.2f}"
            logger.info(
                f"[{self.name}] Local PAPER trade saved: {signal['side']} ${amt_s} shares={shares} fill_price={fill_price:.4f} (slippage impact) on {market.get('question', '')[:50]}"
            )

            return {
                "success": True,
                "trade_id": trade_id,
                "shares_bought": shares,
                "price": fill_price,  # Retorna fill_price em vez de current_price
            }

        except Exception as e:
            logger.error(f"[{self.name}] Local paper trade failed: {e}")
            return {"success": False, "reason": str(e)}

    def _execute_live(self, signal, market, amount, mode):
        """Execute directly on Polymarket CLOB (live trading)."""
        import polymarket_client

        side = signal["side"].lower()
        if side == "yes":
            token_id = market.get("polymarket_token_id")
        else:
            token_id = market.get("polymarket_no_token_id")

        if not token_id:
            logger.error(
                f"[{self.name}] No token ID for side={side} on {market.get('question', '')[:50]}"
            )
            return {"success": False, "reason": "missing_token_id"}

        result = polymarket_client.place_market_order(
            token_id=token_id,
            side=side,
            amount=amount,
        )

        if result.get("success"):
            fill_price = float(result.get("price") or 0.0)
            
            sl_tp_dict = risk_manager.calculate_sl_tp(
                fill_price=fill_price,
                enable_sl_tp=getattr(self, "enable_sl_tp", False),
                sl_pct=getattr(self, "sl_pct", 0.0),
                tp_pct=getattr(self, "tp_pct", 0.0),
                trailing_enabled=getattr(self, "trailing_enabled", False),
                trailing_distance=getattr(self, "trailing_distance", 0.045)
            )

            # --- DEFAULT/FALLBACK VALIDATION ---
            if getattr(self, "enable_sl_tp", False):
                if sl_tp_dict["sl_price"] is None:
                    sl_tp_dict["sl_price"] = max(0.001, fill_price * 0.90)  # Emergência: 10% abaixo
                    logger.warning(f"[{self.name}] Emergência SL ativado: {sl_tp_dict['sl_price']:.3f} (LIVE)")
                if sl_tp_dict["tp_price"] is None:
                    sl_tp_dict["tp_price"] = min(0.999, fill_price * 1.15)  # Emergência: 15% acima
            
            signal["sl_price"] = sl_tp_dict["sl_price"]
            signal["tp_price"] = sl_tp_dict["tp_price"]

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
                sl_price=signal["sl_price"],
                tp_price=signal["tp_price"],
            )
            logger.info(
                f"[{self.name}] LIVE trade: {signal['side']} ${amount} at {result.get('price')} on {market.get('question', '')[:50]}"
            )

        else:
            logger.error(f"[{self.name}] LIVE trade failed: {result.get('error')}")
            # Send Telegram notification for failed trade
            telegram = get_telegram_notifier()
            if telegram:
                telegram.notify_error(
                    self.name, f"Trade failed: {result.get('error', 'Unknown error')}"
                )

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
            if hasattr(self, "_api_key_slot") and self._api_key_slot in bot_keys:
                return bot_keys[self._api_key_slot]
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        # Fallback: default key
        with open(config.SIMMER_API_KEY_PATH) as f:
            return _json.load(f).get("api_key")
