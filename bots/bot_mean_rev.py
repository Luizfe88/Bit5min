"""Bot 2: Mean Reversion strategy (Pure Probability)."""

import math
from bots.base_bot import BaseBot
import config

DEFAULT_PARAMS = {
    "fair_value": 0.50,
    "entry_threshold_yes": 0.42,  # Buy YES if price < 0.42
    "entry_threshold_no": 0.58,   # Buy NO if price > 0.58
    "position_size_pct": 0.02,    # Default size (2% of max pos) — normalizado com outros bots
    "min_confidence": 0.60,
}


class MeanRevBot(BaseBot):
    def __init__(self, name="meanrev-v1", params=None, generation=0, lineage=None):
        super().__init__(
            name=name,
            strategy_type="mean_reversion",
            params=params or DEFAULT_PARAMS.copy(),
            generation=generation,
            lineage=lineage,
        )

    def analyze(self, market: dict, signals: dict) -> dict:
        """
        Pure Mean Reversion:
        Assumes Fair Value = 0.50.
        Buys YES if price < 0.42 (oversold).
        Buys NO if price > 0.58 (overbought).
        """
        # 1. Get Current Price (Probability)
        try:
            # Tenta pegar o preço mais recente possível
            # Se signals['latest'] estiver disponível e for confiável, use-o.
            # Caso contrário, use market['current_price']
            price = float(market.get("current_price") or 0.5)
            
            # Validação básica de range (0.01 a 0.99)
            if not (0.01 < price < 0.99):
                return {"action": "hold", "confidence": 0, "reasoning": f"Price {price:.2f} out of safe range"}
                
        except (ValueError, TypeError):
            return {"action": "hold", "side": "yes", "confidence": 0, "reasoning": "Invalid price data"}

        # 🔒 Trava Anti-Falling Knife: bloqueia zonas extremas/irreversíveis
        if price < 0.25 or price > 0.75:
            return {
                "action": "skip",
                "side": "yes",
                "confidence": 0,
                "reasoning": f"Price {price:.2f} in extreme zone (<0.25 or >0.75). Anti-falling knife active."
            }

        fair_value = self.strategy_params.get("fair_value", 0.50)
        thresh_yes = self.strategy_params.get("entry_threshold_yes", 0.42)
        thresh_no = self.strategy_params.get("entry_threshold_no", 0.58)

        # 2. Check for Reversion Opportunities
        # Case A: Price is too LOW (Oversold) -> Buy YES expecting move to 0.50
        if price < thresh_yes:
            # Confidence increases as price drops further below threshold
            dist = thresh_yes - price
            # Base confidence 0.60 + bonus based on distance
            confidence = min(0.95, 0.60 + (dist * 2.0)) 
            
            return {
                "action": "buy",
                "side": "yes",
                "confidence": confidence,
                "reasoning": f"Oversold: Price {price:.2f} < {thresh_yes} (Target {fair_value})",
                "suggested_amount": self._calc_amount(confidence),
            }

        # Case B: Price is too HIGH (Overbought) -> Buy NO expecting move to 0.50
        elif price > thresh_no:
            # Confidence increases as price rises further above threshold
            dist = price - thresh_no
            confidence = min(0.95, 0.60 + (dist * 2.0))
            
            return {
                "action": "buy",
                "side": "no",
                "confidence": confidence,
                "reasoning": f"Overbought: Price {price:.2f} > {thresh_no} (Target {fair_value})",
                "suggested_amount": self._calc_amount(confidence),
            }

        # Case C: Price is within "Noise" range (0.42 - 0.58) -> HOLD
        return {
            "action": "hold",
            "side": "yes", # Default
            "confidence": 0,
            "reasoning": f"Price {price:.2f} within fair value range ({thresh_yes}-{thresh_no})"
        }

    def _calc_amount(self, confidence):
        import db
        try:
            total_capital = db.get_total_current_capital(self.strategy_params.get("mode", config.get_current_mode()))
        except Exception:
            total_capital = config.PAPER_STARTING_BALANCE
            
        # Use 2% of total capital as base factor
        factor = self.strategy_params.get("position_size_pct", 0.02)

        # Tamanho base calculado (2% do capital atual)
        amount = total_capital * factor

        # Hardcap de segurança: Mean Reversion nunca aloca mais de $350 por trade ou 5% do capital
        HARD_CAP = min(350.0, total_capital * 0.05)
        return min(amount, HARD_CAP)

    def make_decision(self, market: dict, signals: dict) -> dict:
        """Override BaseBot.make_decision to skip ML and use Pure Logic."""
        # Chama analyze diretamente e retorna o resultado sem passar pelo ML do BaseBot
        decision = self.analyze(market, signals)
        
        # Adiciona campos extras que o BaseBot adicionaria (features, etc) se necessário
        # Mas para pure mean reversion, não precisamos de ML features.
        
        return decision
