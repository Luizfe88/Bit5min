import math
import logging

logger = logging.getLogger(__name__)

class SlippageCalculator:
    """Implementa o Square Root Impact Model para Mercado Prediction (Polymarket)."""
    
    @staticmethod
    def calculate_fill_price(side: str, order_amount_usd: float, market_price: float, market_volume_24h: float) -> float:
        """
        Calcula o fill limit (Slippage) com base na liquidez disponível.
        Para Polymarket, liquidity tracker aproxima o volume 24h.
        
        Args:
            side (str): "yes" ou "no"
            order_amount_usd (float): Valor em USD da ordem ($)
            market_price (float): Current price (Probabilidade de YES, range 0.0 a 1.0)
            market_volume_24h (float): Volume em 24 horas no mercado
        
        Returns:
            float: O fill_price (com slippage incluído), da perspectiva do side ("yes" ou "no").
        """
        # Constantes do modelo
        base_spread = 0.005      # 0.5% base spread
        impact_factor = 0.025    # Fator de mercado - mais impacto para orders grandes
        
        # Prevenção de divisão por zero: assumimos floor de 1000 USD de liquidity
        liquidity = max(float(market_volume_24h), 1000.0)
        
        # Impact penalty based on square root of order size vs liquidity
        # slippage_pct = c * sqrt(size / liquidity)
        try:
            impact = impact_factor * math.sqrt(order_amount_usd / liquidity)
        except Exception as e:
            logger.error(f"[SlippageCalculator] Erro calculando sqrt impact_factor: {e}")
            impact = 0.0
            
        slippage_pct = base_spread + impact
        
        # Qual é o preço "verdadeiro" que estou a tentar comprar?
        # Se eu quero "yes", compro yes price = market_price
        # Se eu quero "no", compro no price = (1 - market_price)
        target_price = market_price if side.lower() == "yes" else (1.0 - market_price)
        
        # Limite mínimo: Nunca pode ser menor ou igual a 0 ou vazio
        target_price = max(0.001, target_price)
        
        # Fill price = preco atual + % slippage
        fill_price = target_price + slippage_pct
        
        logger.debug(f"[SlippageCalculator] Side: {side} | Amt: ${order_amount_usd} | Liq: ${liquidity} -> "
                     f"target_price: {target_price:.3f} | impact: {impact:.3f} | fill_price: {fill_price:.3f}")
        
        return fill_price
