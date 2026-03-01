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
    "rsi_overbought_entry": 74,  # v3: Entrada agressiva Down
    "rsi_oversold_entry": 26,  # v3: Entrada agressiva Up
    "rsi_overbought_sl": 81,  # v3: SL se piorar Down
    "rsi_oversold_sl": 19,  # v3: SL se piorar Up
    "ema_short": 20,
    "ema_long": 50,
    "position_size_pct": 0.01,  # 1% por trade
    "min_confidence": 0.65,
    # Trailing TP Configuration
    "trailing_enabled": True,
    "trailing_distance": 0.045,
    "trailing_step": 0.015,
}


class UpDownBot(BaseBot):
    """Bot RSI/EMA para mercados 1h-3d (v3)."""

    def __init__(self, name="updown-rsi-v3", params=None, generation=0, lineage=None):
        super().__init__(
            name=name,
            strategy_type="updown",  # FIX: deve corresponder à chave no arena.py
            params=params or DEFAULT_PARAMS.copy(),
            generation=generation,
            lineage=lineage,
        )

    def _get_risk_profile(self, market_data: dict) -> tuple:
        """Determina o perfil de risco baseado na duração do mercado.
        Retorna (nome_perfil, dict_perfil)"""
        if not market_data:
            return "1d", config.RISK_CONFIG["updown_bot"]["1d"]

        resolves_at_str = market_data.get("resolves_at")

        # Fallback: usar config padrão se não tiver datas
        duration_seconds = 86400  # Default 1d

        try:
            from datetime import datetime

            if resolves_at_str:
                res_dt = datetime.fromisoformat(resolves_at_str.replace("Z", "+00:00"))
                # Se tivermos created_at do mercado, usamos. Se não, usamos created_at do trade como proxy
                start_dt = datetime.utcnow().replace(tzinfo=None)  # Agora como fallback
                if "created_at" in market_data:  # Se o objeto market tiver
                    start_dt = datetime.fromisoformat(
                        market_data["created_at"].replace("Z", "+00:00")
                    )

                if res_dt.tzinfo:
                    res_dt = res_dt.astimezone(None).replace(tzinfo=None)
                if start_dt.tzinfo:
                    start_dt = start_dt.astimezone(None).replace(tzinfo=None)

                duration_seconds = (res_dt - start_dt).total_seconds()
        except Exception as e:
            logger.warning(f"Erro calculando duração para risk profile: {e}")

        duration_hours = duration_seconds / 3600.0

        # Escolher perfil (v3)
        risk_cfg = config.RISK_CONFIG["updown_bot"]
        if duration_hours <= 24:  # 1 dia
            return "1d", risk_cfg["1d"]
        elif duration_hours <= 72:  # 3 dias
            return "3d", risk_cfg["3d"]
        else:  # > 3 dias (Conservador)
            return "conservative", risk_cfg["conservative"]

    def analyze(self, market: dict, signals: dict, kelly_fraction=None) -> dict:
        # 1. Filtro de Mercado (Validar se é Up/Down Crypto)
        question = (market.get("question") or "").lower()
        if not ("up or down" in question or "up/down" in question):
            return self._hold("ignoring non-updown market")

        # Filtro de Confiança Mínima (v3.1)
        conf = signals.get("confidence", 0)
        if conf < 0.20:
            return self._hold(f"low confidence ({conf:.2f})")

        # 1.1 Spread Check (v3 - Rigoroso)
        # Assumindo que signals tem o book ou spread
        # Se o bot não receber o spread, tentamos calcular se possível
        # O arena.py passa 'market' que pode ter best_bid/best_ask
        best_bid = float(market.get("best_bid") or 0)
        best_ask = float(market.get("best_ask") or 0)

        if best_bid > 0 and best_ask > 0:
            mid_price = (best_bid + best_ask) / 2
            spread_pct = (best_ask - best_bid) / mid_price * 100
        else:
            # Fallback for Simmer context spread
            spread_pct = signals.get("orderflow", {}).get("spread_pct")

        if spread_pct is not None:
            max_spread = config.MARKET_FILTER["max_spread_percent"]
            if spread_pct > max_spread:
                return self._hold(
                    f"Spread {spread_pct:.2f}% > {max_spread}% → mercado rejeitado"
                )
        else:
            # Se não temos dados de book nem orderflow spread, assumimos risco ou hold?
            # Por segurança, melhor pular se não sabemos o spread em live
            pass

        # 2. Dados de Preço e Indicadores
        prices = signals.get("prices", [])
        if len(prices) < 60:  # Precisa de histórico para EMA/RSI (pelo menos 50+14)
            return self._hold(f"dados insuficientes ({len(prices)} candles)")

        # Converter para Series do Pandas para facilitar
        series = pd.Series(prices)

        # Calcular Indicadores
        rsi = self._calculate_rsi(series, self.strategy_params["rsi_period"])
        ema_short = (
            series.ewm(span=self.strategy_params["ema_short"], adjust=False)
            .mean()
            .iloc[-1]
        )
        ema_long = (
            series.ewm(span=self.strategy_params["ema_long"], adjust=False)
            .mean()
            .iloc[-1]
        )

        current_price = prices[-1]
        current_rsi = rsi.iloc[-1]

        # 3. Lógica de Trading (RSI + Trend)
        trend = "bull" if ema_short > ema_long else "bear"
        signal = 0
        confidence = 0.0
        reason = []

        # Override thresholds based on aggression level
        agg = (
            config.get_aggression_level()
            if hasattr(config, "get_aggression_level")
            else "medium"
        )
        if agg == "medium":
            entry_oversold = 32
            entry_overbought = 68
        elif agg == "aggressive":
            entry_oversold = 35
            entry_overbought = 65
        else:
            entry_oversold = self.strategy_params.get("rsi_oversold_entry", 26)
            entry_overbought = self.strategy_params.get("rsi_overbought_entry", 74)

        # Cenário A: Reversão de Sobrevenda (Buy UP)
        if current_rsi <= entry_oversold:
            signal += 1
            confidence += 0.65
            reason.append(f"RSI Oversold ({current_rsi:.1f} <= {entry_oversold})")
            # In aggressive mode, EMA confirmation is soft
            if trend == "bull":
                confidence += 0.2
                reason.append("Trend Bull confirm")
            else:
                if agg == "aggressive" and current_rsi <= (entry_oversold - 4):
                    # Strong range entry allowed even without EMA
                    confidence += 0.15
                    reason.append("Strong oversold override (no EMA)")

        # Cenário B: Reversão de Sobrecompra (Buy DOWN)
        elif current_rsi >= entry_overbought:
            signal -= 1
            confidence += 0.65
            reason.append(f"RSI Overbought ({current_rsi:.1f} >= {entry_overbought})")
            if trend == "bear":
                confidence += 0.2
                reason.append("Trend Bear confirm")
            else:
                if agg == "aggressive" and current_rsi >= (entry_overbought + 4):
                    confidence += 0.15
                    reason.append("Strong overbought override (no EMA)")

        # 4. Decisão Final
        # Final decision: apply strategy min_confidence but allow aggression overrides
        strat_min_conf = self.strategy_params.get("min_confidence", 0.65)
        # Lower threshold in aggressive/medium handled at config level if desired
        if abs(signal) < 0.5 or confidence < strat_min_conf:
            return self._hold(
                f"low confidence ({confidence:.2f}) or weak signal ({signal})"
            )

        side = "yes" if signal > 0 else "no"

        # Sizing
        amount = (
            config.PAPER_STARTING_BALANCE * self.strategy_params["position_size_pct"]
        )

        # Determinar perfil de risco para salvar no trade
        profile_name, profile_data = self._get_risk_profile(market)

        return {
            "action": "buy",
            "side": side,
            "confidence": min(confidence, 0.95),
            "reasoning": " | ".join(reason),
            "suggested_amount": amount,
            "trade_features": {
                "risk_profile": profile_name,
                "sl_percent": profile_data["sl_percent"],
                "tp_percent": profile_data["tp_full"],
            },
        }

    def check_exit(
        self, trade: dict, current_price: float, market_data: dict = None
    ) -> dict:
        """
        Verifica condições de saída (SL/TP) dinâmicos v3.
        Chamado pelo PositionMonitorThread do arena.py.
        """
        if not market_data:
            return None

        # Escolher perfil (v3) usando método auxiliar
        # Nota: trade pode não ter created_at se for novo, mas market_data deve ter
        # Vamos passar market_data que é o principal
        _, profile = self._get_risk_profile(market_data)

        # Calcular PnL atual baseado no Fill Price real
        # entry_price = trade["amount"] / trade["shares_bought"] (já considera spread pago)
        shares = trade.get("shares_bought", 0)
        amount = trade.get("amount", 0)

        if shares <= 0 or amount <= 0:
            return None

        fill_price = amount / shares
        side = trade["side"]

        # Preço da share atual (0-1)
        # Se current_price é a probabilidade do "Yes":
        share_price = current_price if side == "yes" else (1.0 - current_price)

        pnl_pct = (share_price - fill_price) / fill_price * 100.0

        # Lógica de Saída v3
        exit_action = None
        reason = ""
        amount_to_sell_pct = 0.0

        # 1. Stop Loss (baseado em % do fill price)
        if pnl_pct <= profile["sl_percent"]:
            exit_action = "sell"
            amount_to_sell_pct = 1.0
            reason = f"SL ativado: {pnl_pct:.1f}% do fill price (Limit: {profile['sl_percent']}%)"

        # 2. Take Profit Partial
        elif pnl_pct >= profile["tp_partial"]:
            exit_action = "sell"
            amount_to_sell_pct = 0.5
            reason = f"TP Parcial: {pnl_pct:.1f}% >= {profile['tp_partial']}%"

            # Se bater TP Full
            if pnl_pct >= profile["tp_full"]:
                amount_to_sell_pct = 1.0
                reason = f"TP Full: {pnl_pct:.1f}% >= {profile['tp_full']}%"

        # 3. Trailing Stop
        # Se PnL > trailing_start, ativa stop se cair trailing_dist do pico
        # Requer HWM (High Water Mark). Sem DB state, usamos o current PnL como proxy simples?
        # Sem HWM persistido, trailing stop puro é difícil.
        # Mas podemos implementar: Se PnL > Trailing Start E PnL < (Trailing Start - Distância)? Não, isso não faz sentido.
        # Trailing stop requer memória do pico.
        # Vamos pular a implementação rigorosa de Trailing sem HWM por enquanto para não introduzir bugs.

        # 4. RSI Stop (v3)
        # Requer RSI atual. O PositionMonitorThread passa 'market_data' que tem 'current_price', mas não histórico.
        # Se não temos histórico aqui, não podemos calcular RSI.
        # Solução ideal: O bot deve manter estado ou acessar feed.
        # Por limitação da arquitetura atual (thread isolada), RSI Stop só funciona se tivermos acesso ao feed.
        # Vamos assumir que não temos RSI no monitor thread por enquanto.

        if exit_action:
            logger.info(
                f"[{self.name}] {reason} | Dur={duration_hours:.1f}h | Fill=${fill_price:.3f} Now=${share_price:.3f}"
            )
            return {
                "action": "sell",
                "amount_pct": amount_to_sell_pct,
                "reason": reason,
            }

        return None

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
