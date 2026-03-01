"""Mean Reversion bot with 25% stop-loss.

Because downside is capped at 25%, this bot trades more aggressively:
- Takes 1.5x larger positions (max loss per trade = 37.5% of normal)
- Trades at lower confidence thresholds (0.03 vs 0.06)
- Willing to take marginal edges that a normal bot would skip
"""

import config
from bots.bot_mean_rev import MeanRevBot, DEFAULT_PARAMS


class MeanRevSLBot(MeanRevBot):
    exit_strategy = "stop_loss"
    stop_loss_pct = 0.25

    def __init__(self, name="meanrev-sl25-v1", params=None, generation=0, lineage=None):
        super().__init__(
            name=name,
            params=params or DEFAULT_PARAMS.copy(),
            generation=generation,
            lineage=lineage,
        )
        self.strategy_type = "mean_reversion_sl"

    def make_decision(self, market, signals):
        """SL bot: more aggressive entries since downside is capped at 25%."""
        decision = super().make_decision(market, signals)

        # 1. Ajuste de Tamanho (Agressividade)
        if decision.get("action") == "buy":
            amount = decision.get("suggested_amount", 0) * 1.5
            decision["suggested_amount"] = min(amount, config.get_max_position())
            decision["reasoning"] += " [SL: 1.5x size]"
            
            if "features" not in decision: decision["features"] = {}
            decision["features"].update({
                "risk_profile": "SL 25% (Fixed)",
                "sl_percent": -self.stop_loss_pct * 100.0
            })

        # 2. Captura de Oportunidades Marginais
        if decision.get("action") == "skip":
            conf = decision.get("confidence", 0)
            if conf >= 0.03:
                market_price = market.get("current_price", 0.5)
                side = decision.get("side", "yes")
                if not ((market_price > 0.65 and side == "no") or (market_price < 0.35 and side == "yes")):
                    max_pos = config.get_max_position()
                    decision["action"] = "buy"
                    decision["side"] = side  # FIX: Ensure side is set to prevent KeyError
                    decision["suggested_amount"] = max_pos * 0.05
                    decision["reasoning"] += " [SL override: marginal edge]"

        # 3. Definição de SL e TP (Novo Sistema)
        if decision.get("action") == "buy":
            features = decision.get("features", {})
            side = decision.get("side", "yes")
            
            # Estimar preço de entrada
            entry_est = 0.5
            try:
                if side == "yes":
                    entry_est = float(features.get("p_entry_yes", market.get("current_price", 0.5)))
                else:
                    mkt_price = float(market.get("current_price", 0.5))
                    entry_est = float(features.get("p_entry_no", 1.0 - mkt_price))
            except (ValueError, TypeError):
                entry_est = 0.5
            
            # Configuração de SL/TP
            sl_pct = 0.10   # 10% de perda máxima
            tp_pct = 0.15   # 15% de lucro alvo
            
            if decision.get("confidence", 0) > 0.15:
                tp_pct = 0.20  # Aumenta alvo para alta convicção
            
            if side == "yes":
                sl_price = entry_est * (1.0 - sl_pct)
                tp_price = entry_est * (1.0 + tp_pct)
            else: # NO
                sl_price = entry_est * (1.0 + sl_pct) # SL acima
                tp_price = entry_est * (1.0 - tp_pct) # TP abaixo
            
            decision["sl_price"] = round(sl_price, 3)
            decision["tp_price"] = round(tp_price, 3)
            decision["reasoning"] += f" [{side} SL@{sl_price:.3f} TP@{tp_price:.3f}]"

        return decision
