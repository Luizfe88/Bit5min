"""
Bot especializado em mercados 'Up or Down' de curto prazo (15min, 1h).

Estratégia: momentum puro do preço do crypto nos últimos N candles.
- Só opera mercados com "up or down" no título
- BTC subindo + mercado Up sub-precificado → YES
- BTC caindo  + mercado Down sub-precificado → NO
- Sem RSI/EMA/Bollinger — não fazem sentido para janelas de 15min
"""

import logging
from strategies.base_bot import BaseBot
import config

logger = logging.getLogger(__name__)

DEFAULT_PARAMS = {
    "lookback_candles":      5,        # 5 candles de 1min = últimos 5 minutos
    "momentum_threshold":    0.0015,   # 0.15% de movimento mínimo para agir
    "max_market_price":      0.72,     # não compra se mercado já precificou >72%
    "min_market_price":      0.28,
    "position_size_pct":     0.06,     # 6% do max position
    "min_confidence":        0.52,
    "volume_confirm_weight": 0.2,
    "momentum_weight":       0.8,
}


class UpDownBot(BaseBot):
    """Bot otimizado para mercados 'X Up or Down' de curto prazo."""

    def __init__(self, name="updown-v1", params=None, generation=0, lineage=None):
        super().__init__(
            name=name,
            strategy_type="momentum",
            params=params or DEFAULT_PARAMS.copy(),
            generation=generation,
            lineage=lineage,
        )

    def analyze(self, market: dict, signals: dict, kelly_fraction=None) -> dict:
        # ── FILTRO: só opera mercados Up or Down OU mercados de curtíssimo prazo ──────────────
        question = (market.get("question") or "").lower()
        is_updown = "up or down" in question
        
        # Se não for "Up or Down", vamos tentar ver se é um mercado rápido de crypto
        # Ex: "Bitcoin > $95k on Feb 26?" que expira em < 1h
        is_short_term = False
        try:
            # Tenta inferir pelo tempo restante se disponível no objeto market
            # O arena.py não passa explicitamente o tempo restante, mas pode ter 'end_date_iso'
            if not is_updown:
                end_iso = market.get("end_date_iso")
                if end_iso:
                    from datetime import datetime, timezone
                    end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
                    now_utc = datetime.now(timezone.utc)
                    if (end_dt - now_utc).total_seconds() < 3600: # Menos de 1h
                        is_short_term = True
        except:
            pass

        if not (is_updown or is_short_term):
            return {
                "action": "skip",
                "reasoning": "não é mercado Up or Down nem curto prazo (<1h)"
            }
            
        prices = signals.get("prices", [])
        volumes = signals.get("volumes", [])
        latest  = signals.get("latest", 0)

        lookback  = int(self.strategy_params.get("lookback_candles", 5))
        threshold = float(self.strategy_params.get("momentum_threshold", 0.0015))

        if len(prices) < lookback or latest == 0:
            return self._hold(f"dados insuficientes ({len(prices)}/{lookback} candles)")

        oldest = prices[-lookback]
        newest = prices[-1]

        if oldest == 0:
            return self._hold("preço zero no histórico")

        pct_change = (newest - oldest) / oldest

        # Força do trend: candles consecutivos na mesma direção
        recent = prices[-lookback:]
        consecutive = 0
        for i in range(1, len(recent)):
            if pct_change > 0 and recent[i] >= recent[i-1]:
                consecutive += 1
            elif pct_change < 0 and recent[i] <= recent[i-1]:
                consecutive += 1
        trend_strength = consecutive / max(len(recent) - 1, 1)

        # Confirmação de volume
        vol_signal = 0.5
        if len(volumes) >= lookback * 2:
            recent_vol = sum(volumes[-lookback:])
            prev_vol   = sum(volumes[-lookback*2:-lookback])
            if prev_vol > 0:
                vol_signal = min(1.0, (recent_vol / prev_vol) * 0.5)

        mw = float(self.strategy_params.get("momentum_weight", 0.8))
        vw = float(self.strategy_params.get("volume_confirm_weight", 0.2))
        momentum_conf = min(1.0, abs(pct_change) / max(threshold, 0.0001) * 0.5 + trend_strength * 0.5)
        confidence    = min(0.95, momentum_conf * mw + vol_signal * vw)

        market_price = float(market.get("current_price") or market.get("lastTradePrice") or 0.5)
        max_price    = float(self.strategy_params.get("max_market_price", 0.72))
        min_price    = float(self.strategy_params.get("min_market_price", 0.28))
        min_conf     = float(self.strategy_params.get("min_confidence", 0.52))

        if abs(pct_change) < threshold:
            return self._hold(f"momentum {pct_change:+.4f} abaixo do threshold {threshold}")

        amount = config.get_max_position() * float(self.strategy_params.get("position_size_pct", 0.06))
        if kelly_fraction:
            amount *= kelly_fraction

        # Momentum positivo → crypto subindo → YES (Up)
        if pct_change > 0:
            if market_price > max_price:
                return self._hold(f"UP já precificado: mkt={market_price:.2f} > max={max_price}")
            if confidence < min_conf:
                return self._hold(f"conf {confidence:.2f} < min {min_conf}")
            edge       = max_price - market_price
            final_conf = min(0.95, confidence + edge * 0.3)
            return {
                "action": "buy", "side": "yes",
                "confidence": final_conf,
                "reasoning": (f"UpDown UP: {pct_change:+.4f} ({lookback}c), "
                              f"trend={trend_strength:.2f}, mkt={market_price:.2f}"),
                "suggested_amount": float(amount),
            }

        # Momentum negativo → crypto caindo → NO (Down)
        if pct_change < 0:
            no_price = 1.0 - market_price
            if no_price > max_price:
                return self._hold(f"DOWN já precificado: no={no_price:.2f} > max={max_price}")
            if market_price < min_price:
                return self._hold(f"mercado já muito DOWN: yes={market_price:.2f}")
            if confidence < min_conf:
                return self._hold(f"conf {confidence:.2f} < min {min_conf}")
            edge       = max_price - no_price
            final_conf = min(0.95, confidence + edge * 0.3)
            return {
                "action": "buy", "side": "no",
                "confidence": final_conf,
                "reasoning": (f"UpDown DOWN: {pct_change:+.4f} ({lookback}c), "
                              f"trend={trend_strength:.2f}, mkt={market_price:.2f}"),
                "suggested_amount": float(amount),
            }

        return self._hold("sem sinal direcional")

    def _hold(self, reason: str) -> dict:
        return {"action": "hold", "side": "yes", "confidence": 0.0,
                "reasoning": reason, "suggested_amount": 0.0}

    def mutate(self, params: dict) -> dict:
        import random
        p = params.copy()
        mutations = {
            "lookback_candles":   lambda v: max(3, min(15, int(v + random.randint(-2, 2)))),
            "momentum_threshold": lambda v: max(0.0005, min(0.005, v * random.uniform(0.7, 1.4))),
            "max_market_price":   lambda v: max(0.55, min(0.85, v + random.uniform(-0.05, 0.05))),
            "position_size_pct":  lambda v: max(0.02, min(0.15, v * random.uniform(0.8, 1.2))),
            "min_confidence":     lambda v: max(0.50, min(0.75, v + random.uniform(-0.05, 0.05))),
        }
        for key, fn in mutations.items():
            if key in p and random.random() < 0.5:
                p[key] = fn(p[key])
        return p
