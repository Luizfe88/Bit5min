"""
Bot especializado em mercados 'Up or Down' com janela de 1h-3d (Sweet Spot).

Estratégia: RSI + EMA + Momentum
- Opera mercados Up/Down de Bitcoin/Ethereum/Solana
- Timeframe ideal: 1h a 3 dias
- Usa RSI (14) para identificar sobrecompra/sobrevenda
- Usa EMA (20/50) para filtro de tendência
- Entra quando RSI indica reversão ou continuação forte
"""

import logging
import pandas as pd
import numpy as np
from bots.base_bot import BaseBot
import config

logger = logging.getLogger(__name__)

DEFAULT_PARAMS = {
    "rsi_period": 14,
    "rsi_overbought": 70,
    "rsi_oversold": 30,
    "ema_short": 20,
    "ema_long": 50,
    "position_size_pct": 0.02,     # 2% por trade (conservador)
    "min_confidence": 0.60,
    "stop_loss_rsi_reversal": True, # Sai se RSI inverter
    "take_profit_rsi_target": 50,   # Sai parcial se RSI voltar ao meio
}

class UpDownBot(BaseBot):
    """Bot RSI/EMA para mercados 1h-3d."""

    def __init__(self, name="updown-rsi-v2", params=None, generation=0, lineage=None):
        super().__init__(
            name=name,
            strategy_type="rsi_trend",
            params=params or DEFAULT_PARAMS.copy(),
            generation=generation,
            lineage=lineage,
        )

    def analyze(self, market: dict, signals: dict, kelly_fraction=None) -> dict:
        # 1. Filtro de Mercado (Validar se é Up/Down Crypto)
        question = (market.get("question") or "").lower()
        if not ("up or down" in question or "up/down" in question):
             return self._hold("ignoring non-updown market")

        # 2. Dados de Preço e Indicadores
        prices = signals.get("prices", [])
        if len(prices) < 60: # Precisa de histórico para EMA/RSI (pelo menos 50+14)
            return self._hold(f"dados insuficientes ({len(prices)} candles)")

        # Converter para Series do Pandas para facilitar
        series = pd.Series(prices)
        
        # Calcular Indicadores
        rsi = self._calculate_rsi(series, self.strategy_params["rsi_period"])
        ema_short = series.ewm(span=self.strategy_params["ema_short"], adjust=False).mean().iloc[-1]
        ema_long = series.ewm(span=self.strategy_params["ema_long"], adjust=False).mean().iloc[-1]
        
        current_price = prices[-1]
        current_rsi = rsi.iloc[-1]
        
        # 3. Lógica de Trading (RSI + Trend)
        trend = "bull" if ema_short > ema_long else "bear"
        signal = 0
        confidence = 0.0
        reason = []

        # Cenário A: Reversão de Sobrevenda (Buy UP)
        # RSI < 30 e cruzando pra cima, em tendência de alta macro (ou repique)
        if current_rsi < self.strategy_params["rsi_oversold"]:
            signal += 1
            confidence += 0.6
            reason.append(f"RSI Oversold ({current_rsi:.1f})")
            if trend == "bull":
                confidence += 0.2
                reason.append("Trend Bull confirm")

        # Cenário B: Reversão de Sobrecompra (Buy DOWN)
        # RSI > 70 e cruzando pra baixo
        elif current_rsi > self.strategy_params["rsi_overbought"]:
            signal -= 1
            confidence += 0.6
            reason.append(f"RSI Overbought ({current_rsi:.1f})")
            if trend == "bear":
                confidence += 0.2
                reason.append("Trend Bear confirm")
                
        # Cenário C: Momentum Forte (RSI entre 40-60 mas rompendo)
        # Se preço > EMA Curta > EMA Longa e RSI > 50 (Força comprador)
        elif trend == "bull" and current_rsi > 50 and current_rsi < 70:
            signal += 0.5
            confidence += 0.4
            reason.append(f"Bull Trend Momentum (RSI {current_rsi:.1f})")
            
        elif trend == "bear" and current_rsi < 50 and current_rsi > 30:
            signal -= 0.5
            confidence += 0.4
            reason.append(f"Bear Trend Momentum (RSI {current_rsi:.1f})")

        # 4. Decisão Final
        if abs(signal) < 0.5 or confidence < self.strategy_params["min_confidence"]:
             return self._hold(f"low confidence ({confidence:.2f}) or weak signal ({signal})")
             
        side = "yes" if signal > 0 else "no"
        
        # Sizing
        amount = config.PAPER_STARTING_BALANCE * self.strategy_params["position_size_pct"]
        
        return {
            "action": "buy",
            "side": side,
            "confidence": min(confidence, 0.95),
            "reasoning": " | ".join(reason),
            "suggested_amount": amount
        }

    def _calculate_rsi(self, series, period):
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    def _hold(self, reason):
        return {"action": "hold", "reasoning": reason}

    def mutate(self, params: dict) -> dict:
        import random
        p = params.copy()
        if random.random() < 0.5:
            p["rsi_period"] = random.choice([7, 14, 21])
        if random.random() < 0.5:
            p["ema_short"] = random.randint(10, 30)
        if random.random() < 0.5:
            p["ema_long"] = random.randint(40, 100)
        if random.random() < 0.5:
            p["rsi_overbought"] = random.randint(65, 80)
            p["rsi_oversold"] = random.randint(20, 35)
        return p
