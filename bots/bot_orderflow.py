"""
Orderflow Bot - Análise de Fluxo de Ordens para Polymarket
"""

import logging
import time
import math
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

from bots.base_bot import BaseBot
import config
import db

logger = logging.getLogger(__name__)

DEFAULT_PARAMS = {
    "flow_sensitivity": 0.3,
    "min_volume_threshold": 500,
    "analysis_period": 15,
    "min_buy_sell_ratio": 1.5,
    "max_buy_sell_ratio": 5.0,
    "whale_order_size": 500,
    "whale_weight": 1.5,
    "confidence_threshold": 0.65,
    "max_hold_time": 4,
    "stop_loss_pct": 0.08,
    "take_profit_pct": 0.12,
    # Trailing TP Configuration (Scalping Rápido)
    "trailing_enabled": True,
    "trailing_distance": 0.03,  # 3% de distância (apertado para scalping)
    "trailing_step": 0.01,      # Atualiza a cada 1% de lucro
}

class OrderflowBot(BaseBot):
    """
    Bot baseado em análise de fluxo de ordens.
    
    Esta estratégia analisa:
    - Volume de ordens de compra vs venda
- Tamanho médio das ordens
    - Pressão de compra/venda
    - Mudanças no fluxo de ordens
    """
    
    def __init__(self, name: str, params: Dict[str, Any] = None, generation: int = 0, lineage: str = None):
        merged_params = {**DEFAULT_PARAMS, **(params or {})}
        super().__init__(
            name=name,
            strategy_type="orderflow",
            params=merged_params,
            generation=generation,
            lineage=lineage
        )
        
        # Parâmetros padrão
        self.params = merged_params
        
        # Cache de dados de fluxo
        self.flow_cache = {}
        self.last_analysis = {}
        
        logger.info(f"🌊 OrderflowBot '{name}' inicializado com params: {self.params}")
    
    def analyze(self, market: Dict[str, Any], signals: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analisa o mercado e decide se deve entrar em uma posição.
        
        Args:
            market: Dados do mercado (preço, volume, etc)
            signals: Sinais de outros bots/feeds (não usado aqui diretamente, pois usamos o feed interno)
            
        Returns:
            Dict com ação ('buy', 'sell', 'hold'), lado ('yes', 'no'), confiança e reasoning.
        """
        # Delegar para generate_signal que já implementa a lógica
        signal_data = self.generate_signal(market)
        
        # Mapear retorno de generate_signal para o formato esperado pelo BaseBot
        signal_val = signal_data.get("signal", 0)
        confidence = signal_data.get("confidence", 0)
        reason = signal_data.get("reason", "no signal")
        
        if abs(signal_val) < 0.1:
            return {
                "action": "hold",
                "side": "yes", # Default
                "confidence": 0,
                "reasoning": reason
            }
            
        # Determinar lado
        side = "yes" if signal_val > 0 else "no"
        
        # Calcular tamanho da posição sugerida
        import db
        try:
            total_cap = db.get_total_current_capital(config.get_current_mode())
        except Exception:
            total_cap = config.PAPER_STARTING_BALANCE
        amount = total_cap * 0.02  # Default 2% para este bot se não houver cálculo de força
        
        return {
            "action": "buy",
            "side": side,
            "confidence": confidence,
            "reasoning": f"Orderflow signal={signal_val:.2f}: {reason}",
            "suggested_amount": amount
        }

    def generate_signal(self, market: Dict[str, Any]) -> Dict[str, Any]:
        """
        Gera sinal baseado em análise de fluxo de ordens.
        """
        market_id = market.get('id') or market.get('market_id')
        if not market_id:
            logger.error(f"OrderflowBot: market_id not found in {market.keys()}")
            return {"signal": 0, "confidence": 0, "reason": "missing_market_id"}
        
        # Obter dados de fluxo
        flow_data = self._get_orderflow_data(market_id)
        if not flow_data:
            return {"signal": 0, "confidence": 0, "reason": "no_flow_data"}
        
        # Analisar fluxo
        analysis = self._analyze_flow(flow_data)
        if not analysis:
            return {"signal": 0, "confidence": 0, "reason": "analysis_failed"}
        
        # Calcular sinal baseado na análise
        signal = self._calculate_signal(analysis, market)
        
        # Adicionar metadados
        signal.update({
            "analysis": analysis,
            "flow_data": flow_data,
            "market_price": market.get("p_yes", 0.5),
            "timestamp": time.time()
        })
        
        return signal
    
    def _get_orderflow_data(self, market_id: str) -> Optional[Dict[str, Any]]:
        """
        Obtém dados de fluxo de ordens do mercado.
        """
        try:
            # Obter do feed de orderflow
            from signals.orderflow import get_feed
            feed = get_feed()
            
            if not feed:
                return None
                
            # Se não estiver no cache, força atualização
            if market_id not in feed.data:
                # Precisa de API key? O feed usa se tiver, senão simula
                # Onde pegar API key? Do config ou self.api_key se disponível
                # BaseBot não armazena api_key geralmente, mas arena sim.
                # Vamos tentar chamar sem api_key primeiro (vai simular)
                feed.get_signals(market_id)
            
            return feed.data.get(market_id)
            
        except Exception as e:
            logger.error(f"Erro ao obter dados de orderflow para {market_id}: {e}")
            return None
    
    def _analyze_flow(self, flow_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Analisa os dados de fluxo e extrai insights.
        """
        try:
            # Extrair métricas básicas
            buy_volume = flow_data.get("buy_volume", 0)
            sell_volume = flow_data.get("sell_volume", 0)
            buy_orders = flow_data.get("buy_orders", 0)
            sell_orders = flow_data.get("sell_orders", 0)
            
            # Calcular volumes e ratios
            total_volume = buy_volume + sell_volume
            
            if total_volume < self.params["min_volume_threshold"]:
                return {"reason": "low_volume", "total_volume": total_volume}
            
            # Ratio de compra/venda
            buy_sell_ratio = buy_volume / sell_volume if sell_volume > 0 else float('inf')
            
            # Tamanho médio das ordens
            avg_buy_size = buy_volume / buy_orders if buy_orders > 0 else 0
            avg_sell_size = sell_volume / sell_orders if sell_orders > 0 else 0
            
            # Identificar "whales" (ordens grandes)
            whale_buy_orders = flow_data.get("whale_buy_orders", 0)
            whale_sell_orders = flow_data.get("whale_sell_orders", 0)
            whale_buy_volume = flow_data.get("whale_buy_volume", 0)
            whale_sell_volume = flow_data.get("whale_sell_volume", 0)
            
            # Calcular pressão
            buy_pressure = self._calculate_pressure(
                volume=buy_volume,
                orders=buy_orders,
                whale_volume=whale_buy_volume,
                whale_orders=whale_buy_orders,
                avg_size=avg_buy_size
            )
            
            sell_pressure = self._calculate_pressure(
                volume=sell_volume,
                orders=sell_orders,
                whale_volume=whale_sell_volume,
                whale_orders=whale_sell_orders,
                avg_size=avg_sell_size
            )
            
            # Net pressure (positivo = compra dominante, negativo = venda dominante)
            net_pressure = buy_pressure - sell_pressure
            
            # Mudança na pressão vs período anterior
            pressure_change = self._calculate_pressure_change(flow_data)
            
            return {
                "buy_volume": buy_volume,
                "sell_volume": sell_volume,
                "total_volume": total_volume,
                "buy_sell_ratio": buy_sell_ratio,
                "avg_buy_size": avg_buy_size,
                "avg_sell_size": avg_sell_size,
                "buy_pressure": buy_pressure,
                "sell_pressure": sell_pressure,
                "net_pressure": net_pressure,
                "pressure_change": pressure_change,
                "whale_buy_orders": whale_buy_orders,
                "whale_sell_orders": whale_sell_orders,
                "whale_ratio": whale_buy_orders / whale_sell_orders if whale_sell_orders > 0 else float('inf')
            }
            
        except Exception as e:
            logger.error(f"Erro na análise de fluxo: {e}")
            return None
    
    def _calculate_signal(self, analysis: Dict[str, Any], market: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calcula o sinal final e a confiança.
        """
        net_pressure = analysis["net_pressure"]
        buy_sell_ratio = analysis["buy_sell_ratio"]
        pressure_change = analysis.get("pressure_change", 0)
        
        # Lógica de sinal
        signal = 0
        confidence = 0
        reason = []
        
        # Pressão líquida
        if abs(net_pressure) > 0.5:
            signal += net_pressure * self.params["flow_sensitivity"]
            reason.append(f"pressure={net_pressure:.2f}")
            
        # Ratio de compra/venda
        if buy_sell_ratio > self.params["min_buy_sell_ratio"]:
            signal += 0.2
            reason.append(f"high_buy_ratio={buy_sell_ratio:.1f}")
        elif buy_sell_ratio < (1 / self.params["min_buy_sell_ratio"]):
            signal -= 0.2
            reason.append(f"high_sell_ratio={buy_sell_ratio:.1f}")
            
        # Mudança de pressão
        if abs(pressure_change) > 0.2:
            signal += pressure_change * 0.5
            reason.append(f"pressure_change={pressure_change:.2f}")
            
        # Calcular confiança
        confidence = min(abs(signal), 1.0)
        
        return {
            "signal": signal,
            "confidence": confidence,
            "reason": ", ".join(reason)
        }
        
    def _calculate_pressure(self, volume: float, orders: int, whale_volume: float, 
                         whale_orders: int, avg_size: float) -> float:
        """
        Calcula pressão de compra ou venda com base em múltiplos fatores.
        """
        # Fator volume (0-1)
        volume_factor = min(volume / (self.params["min_volume_threshold"] * 5), 1.0)
        
        # Fator whales
        whale_factor = (whale_volume / volume) * self.params["whale_weight"] if volume > 0 else 0
        
        return (volume_factor + whale_factor) / 2
    
    def _calculate_pressure_change(self, flow_data: Dict[str, Any]) -> float:
        """
        Calcula a mudança na pressão em relação ao período anterior.
        """
        # Simplificado para este exemplo, idealmente usaria histórico
        return 0.0
    
    def _calculate_signal(self, analysis: Dict[str, Any], market: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calcula sinal final baseado na análise de fluxo.
        """
        # Verificar se temos dados suficientes
        if "reason" in analysis:
            return {"signal": 0, "confidence": 0, "reason": analysis["reason"]}
        
        # Extrair métricas principais
        net_pressure = analysis["net_pressure"]
        buy_sell_ratio = analysis["buy_sell_ratio"]
        pressure_change = analysis["pressure_change"]
        
        # Verificar thresholds
        if buy_sell_ratio < self.params["min_buy_sell_ratio"] and buy_sell_ratio > (1 / self.params["max_buy_sell_ratio"]):
            return {"signal": 0, "confidence": 0, "reason": "ratio_outside_thresholds"}
        
        # Calcular força do sinal (-1 a 1)
        # Positivo = sinal de compra (YES), Negativo = sinal de venda (NO)
        
        # Baseado na pressão líquida
        pressure_signal = net_pressure * self.params["flow_sensitivity"]
        
        # Baseado na mudança de pressão (momentum)
        change_signal = pressure_change * 0.3
        
        # Baseado no ratio compra/venda
        if buy_sell_ratio > 1:
            ratio_signal = min((buy_sell_ratio - 1) / (self.params["max_buy_sell_ratio"] - 1), 1.0)
        else:
            ratio_signal = -min((1 - buy_sell_ratio) / (1 - 1/self.params["max_buy_sell_ratio"]), 1.0)
        
        # Sinal combinado
        signal = (pressure_signal * 0.5 + change_signal * 0.2 + ratio_signal * 0.3)
        
        # Limitar entre -1 e 1
        signal = max(-1.0, min(1.0, signal))
        
        # Calcular confiança (0 a 1)
        confidence = abs(signal)
        
        # Ajustar confiança baseado na qualidade dos dados
        if analysis["total_volume"] < self.params["min_volume_threshold"] * 2:
            confidence *= 0.7
        
        # Verificar threshold de confiança
        if confidence < self.params["confidence_threshold"]:
            return {"signal": 0, "confidence": confidence, "reason": "low_confidence"}
        
        # Determinar direção e força
        direction = "YES" if signal > 0 else "NO"
        strength = abs(signal)
        
        # Calcular tamanho sugerido do trade
        suggested_amount = self._calculate_position_size(strength, market)
        
        return {
            "signal": signal,
            "confidence": confidence,
            "direction": direction,
            "strength": strength,
            "suggested_amount": suggested_amount,
            "reason": "orderflow_signal",
            "analysis_summary": {
                "net_pressure": net_pressure,
                "buy_sell_ratio": buy_sell_ratio,
                "pressure_change": pressure_change,
                "total_volume": analysis["total_volume"]
            }
        }
    
    def _calculate_position_size(self, strength: float, market: Dict[str, Any]) -> float:
        """
        Calcula tamanho da posição baseado na força do sinal.
        """
        # Obter limites de risco
        max_pos = self._get_max_position_size()
        
        # Tamanho baseado na força do sinal (30% a 80% do máximo)
        base_size = max_pos * (0.3 + strength * 0.5)
        
        # Ajustar baseado na volatilidade do mercado (se disponível)
        volatility = market.get("volatility", 0.05)
        if volatility > 0.1:  # Alta volatilidade
            base_size *= 0.7
        
        return base_size
    
    def _get_max_position_size(self) -> float:
        """
        Obtém tamanho máximo de posição baseado na banca atual.
        """
        import db
        import config
        try:
            total_cap = db.get_total_current_capital(config.get_current_mode())
            return total_cap * 0.02 # Hard cap de 2% do capital total
        except Exception:
            return 50.0
    
    def should_exit_position(self, position: Dict[str, Any], market: Dict[str, Any]) -> Dict[str, Any]:
        """
        Decide se deve sair de uma posição baseada em condições de saída.
        """
        # Verificar stop loss e take profit
        exit_check = super().should_exit_position(position, market)
        if exit_check["should_exit"]:
            return exit_check
        
        # Verificar reversão no fluxo
        try:
            market_id = position["market_id"]
            flow_data = self._get_orderflow_data(market_id)
            
            if not flow_data:
                return {"should_exit": False, "reason": "no_flow_data"}
            
            # Analisar fluxo atual
            analysis = self._analyze_flow(flow_data)
            if not analysis or "net_pressure" not in analysis:
                return {"should_exit": False, "reason": "analysis_failed"}
            
            # Verificar reversão
            current_pressure = analysis["net_pressure"]
            entry_pressure = position.get("entry_flow_pressure", current_pressure)
            
            # Se a pressão reverteu significativamente, considerar saída
            pressure_change = (current_pressure - entry_pressure) / abs(entry_pressure) if entry_pressure != 0 else 0
            
            if abs(pressure_change) > 0.5:  # 50% de reversão
                return {
                    "should_exit": True,
                    "reason": "flow_reversal",
                    "pressure_change": pressure_change
                }
            
            # Verificar tempo máximo de holding
            entry_time = datetime.fromisoformat(position["created_at"])
            max_hold = timedelta(hours=self.params["max_hold_time"])
            
            if datetime.now() - entry_time > max_hold:
                return {
                    "should_exit": True,
                    "reason": "max_hold_time_reached"
                }
            
            return {"should_exit": False, "reason": "hold"}
            
        except Exception as e:
            logger.error(f"Erro ao verificar saída por fluxo: {e}")
            return {"should_exit": False, "reason": "error"}
    
    def get_strategy_description(self) -> str:
        """Retorna descrição da estratégia."""
        return f"""
Orderflow Bot - Análise de Fluxo de Ordens

Parâmetros atuais:
- Sensibilidade: {self.params['flow_sensitivity']}
- Volume mínimo: ${self.params['min_volume_threshold']}
- Período análise: {self.params['analysis_period']}min
- Threshold compra: {self.params['min_buy_sell_ratio']}
- Max hold: {self.params['max_hold_time']}h
- Stop: {self.params['stop_loss_pct']*100}%
- Target: {self.params['take_profit_pct']*100}%

Estratégia: Analisa volume, tamanho e direção das ordens para identificar 
pressão de compra/venda antes de executar trades.
        """.strip()