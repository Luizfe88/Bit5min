import requests
import logging
import time

logger = logging.getLogger(__name__)

class PriceOracle:
    """
    Price Oracle to fetch klines from Binance and calculate directional evidence.
    """
    BINANCE_URL = "https://api.binance.com/api/v3/klines"

    @staticmethod
    def get_binance_movement(symbol="BTCUSDT", interval="5m"):
        """
        Fetches the last 5m movement from Binance.
        Returns: percentage movement (e.g., 0.005 for +0.5%)
        """
        try:
            params = {
                "symbol": symbol,
                "interval": interval,
                "limit": 2
            }
            response = requests.get(PriceOracle.BINANCE_URL, params=params, timeout=5)
            if response.status_code == 200:
                klines = response.json()
                if len(klines) >= 2:
                    # kline: [open_time, open, high, low, close, volume, ...]
                    last_close = float(klines[-2][4])
                    current_close = float(klines[-1][4])
                    movement = (current_close - last_close) / last_close
                    return movement
        except Exception as e:
            logger.error(f"Error fetching Binance movement: {e}")
        return 0.0

    @staticmethod
    def apply_bayesian_update(p_prior, movement, sensitivity=2.0):
        """
        Applies Bayesian formula: P(H|E) = P(E|H) * P(H) / P(E)
        Simplified for directional movement evidence.
        
        Args:
            p_prior: Current Polymarket Price (Prior)
            movement: Binance 5m movement (float)
            sensitivity: How much we trust the Binance signal
            
        Returns:
            p_model: Updated probability
        """
        # Mapping movement to conditional likelihood
        # If movement is positive, P(E|H_yes) > P(E|H_no)
        # We use a logistic-like adjustment
        edge = movement * sensitivity
        
        # Bayesian update (Simplified Odds form)
        # Odds_post = Odds_prior * Likelihood_ratio
        # Likelihood ratio based on directional alignment
        
        if p_prior <= 0 or p_prior >= 1:
            return p_prior
            
        odds_prior = p_prior / (1.0 - p_prior)
        
        # Likelihood ratio: e^(edge)
        # If BTC goes up 1%, likelihood of 'YES' increases
        l_ratio = 1.0 + (edge * 5.0) # Scaled linear approximation for small movements
        l_ratio = max(0.2, min(5.0, l_ratio))
        
        odds_post = odds_prior * l_ratio
        p_post = odds_post / (1.0 + odds_post)
        
        return max(0.01, min(0.99, p_post))
