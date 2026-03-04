"""Hybrid / Ensemble strategy combining technical signals."""

from bots.base_bot import BaseBot
from bots.bot_momentum import MomentumBot
from bots.bot_mean_rev import MeanRevBot

DEFAULT_PARAMS = {
    "momentum_weight": 0.50,
    "mean_rev_weight": 0.50,
    "sentiment_weight": 0.0,
    "confidence_threshold": 0.40,
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

        # Allow dynamic extra weight to mean_rev when it's highly confident
        mr_w = self.strategy_params.get("mean_rev_weight", 0.5)
        try:
            if (
                mr_signal
                and mr_signal.get("action") != "hold"
                and float(mr_signal.get("confidence", 0)) >= 0.75
            ):
                mr_w = min(1.0, mr_w + 0.15)
        except Exception:
            pass

        sub_signals = [
            (mom_signal, self.strategy_params.get("momentum_weight", 0.5)),
            (mr_signal, mr_w),
        ]

        weighted_score = 0
        active_signals = 0
        reasons = []

        valid_signals = []
        skipped_weight = 0.0

        for sig, weight in sub_signals:
            action = sig.get("action", "hold")
            if action in ("hold", "skip"):
                if action == "skip":
                    skipped_weight += weight
                continue
            valid_signals.append({"sig": sig, "weight": weight})

        if valid_signals and skipped_weight > 0.0:
            extra = skipped_weight / len(valid_signals)
            for item in valid_signals:
                item["weight"] += extra

        weighted_score = 0
        active_signals = len(valid_signals)
        reasons = []
        yes_votes = 0
        no_votes = 0

        for item in valid_signals:
            sig = item["sig"]
            weight = item["weight"]
            direction = 1 if sig.get("side", "yes") == "yes" else -1
            weighted_score += direction * float(sig.get("confidence", 0.0)) * weight
            reasons.append(f"{sig.get('reasoning', '')[:60]}")
            if sig.get("side", "yes") == "yes":
                yes_votes += 1
            else:
                no_votes += 1

        if active_signals == 0:
            return {
                "action": "hold",
                "side": "yes",
                "confidence": 0,
                "reasoning": "All sub-strategies say hold or skip",
            }

        # Consensus rule: accept if at least 2/3 of active signals agree OR simple majority + weighted score
        active_count = active_signals
        required = 2 if active_count >= 3 else 1
        agreement = max(yes_votes, no_votes) >= required or abs(weighted_score) >= 0.35

        confidence = abs(weighted_score)
        if agreement:
            confidence += self.strategy_params["agreement_bonus"]
        confidence = min(0.95, confidence)

        # Use either strategy threshold or global min confidence as lower bound
        import config

        threshold = max(
            self.strategy_params.get("confidence_threshold", 0.6),
            config.get_min_confidence()
            if hasattr(config, "get_min_confidence")
            else 0.5,
        )
        if confidence < threshold:
            return {
                "action": "hold",
                "side": "yes",
                "confidence": confidence,
                "reasoning": f"Ensemble confidence {confidence:.2f} below threshold {threshold:.2f}",
            }

        side = "yes" if weighted_score > 0 else "no"
        import config

        amount = config.get_max_position() * self.strategy_params["position_size_pct"]

        return {
            "action": "buy",
            "side": side,
            "confidence": confidence,
            "reasoning": f"Ensemble ({yes_votes}Y/{no_votes}N, agree={agreement}): "
            + " | ".join(reasons),
            "suggested_amount": amount,
        }
