"""Bot 2: Mean Reversion strategy (Pure Probability)."""

import math
from bots.base_bot import BaseBot
import config

DEFAULT_PARAMS = {
    "fair_value": 0.50,
    "entry_threshold_yes": 0.42,  # Buy YES if price < 0.42
    "entry_threshold_no": 0.58,   # Buy NO if price > 0.58
    "position_size_pct": 0.10,    # Default size (10% of max pos)
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
        base_size = config.get_max_position()
        factor = self.strategy_params.get("position_size_pct", 0.10)
        
        # Se confiança muito alta (> 0.85), dobra o tamanho
        if confidence > 0.85:
            factor *= 1.5
            
        return base_size * factor

    def make_decision(self, market: dict, signals: dict) -> dict:
        """Override BaseBot.make_decision to skip ML and use Pure Logic."""
        # Chama analyze diretamente e retorna o resultado sem passar pelo ML do BaseBot
        decision = self.analyze(market, signals)
        
        # Adiciona campos extras que o BaseBot adicionaria (features, etc) se necessário
        # Mas para pure mean reversion, não precisamos de ML features.
        
        return decision
