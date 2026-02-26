"""Hybrid / Ensemble strategy combining technical signals."""

from bots.base_bot import BaseBot
from bots.bot_momentum import MomentumBot
from bots.bot_mean_rev import MeanRevBot

DEFAULT_PARAMS = {
    "momentum_weight": 0.50,
    "mean_rev_weight": 0.50,
    "sentiment_weight": 0.0,
    "confidence_threshold": 0.60,
    "agreement_bonus": 0.15,
    "position_size_pct": 0.05,
    "min_confidence": 0.5,
    # Trailing TP Configuration
    "trailing_enabled": True,
    "trailing_distance": 0.045,
    "trailing_step": 0.015,
}


class HybridBot(BaseBot):
    def __init__(self, name="hybrid-v1", params=None, generation=0, lineage=None):
        super().__init__(
            name=name,
            strategy_type="hybrid",
            params=params or DEFAULT_PARAMS.copy(),
            generation=generation,
            lineage=lineage,
        )
        self._momentum = MomentumBot(name="_internal_mom")
        self._mean_rev = MeanRevBot(name="_internal_mr")

    def analyze(self, market: dict, signals: dict) -> dict:
        """Combine signals from momentum and mean reversion."""
        mom_signal = self._momentum.analyze(market, signals)
        mr_signal = self._mean_rev.analyze(market, signals)

        sub_signals = [
            (mom_signal, self.strategy_params["momentum_weight"]),
            (mr_signal, self.strategy_params["mean_rev_weight"]),
        ]

        weighted_score = 0
        active_signals = 0
        reasons = []

        for sig, weight in sub_signals:
            if sig["action"] == "hold":
                continue
            active_signals += 1
            direction = 1 if sig["side"] == "yes" else -1
            weighted_score += direction * sig["confidence"] * weight
            reasons.append(f"{sig.get('reasoning', '')[:60]}")

        if active_signals == 0:
            return {"action": "hold", "side": "yes", "confidence": 0,
                    "reasoning": "All sub-strategies say hold"}

        yes_votes = sum(1 for s, _ in sub_signals if s["action"] != "hold" and s["side"] == "yes")
        no_votes = sum(1 for s, _ in sub_signals if s["action"] != "hold" and s["side"] == "no")
        agreement = max(yes_votes, no_votes) >= 2

        confidence = abs(weighted_score)
        if agreement:
            confidence += self.strategy_params["agreement_bonus"]
        confidence = min(0.95, confidence)

        threshold = self.strategy_params["confidence_threshold"]
        if confidence < threshold:
            return {"action": "hold", "side": "yes", "confidence": confidence,
                    "reasoning": f"Ensemble confidence {confidence:.2f} below threshold {threshold}"}

        side = "yes" if weighted_score > 0 else "no"
        import config
        amount = config.get_max_position() * self.strategy_params["position_size_pct"]

        return {
            "action": "buy",
            "side": side,
            "confidence": confidence,
            "reasoning": f"Ensemble ({yes_votes}Y/{no_votes}N, agree={agreement}): " + " | ".join(reasons),
            "suggested_amount": amount,
        }
