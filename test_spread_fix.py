import sys
from pathlib import Path
import logging

# Add root to path
sys.path.insert(0, str(Path(__file__).parent.resolve()))

from bots.bot_momentum import MomentumBot
import config

# Setup a dummy logger to capture output
logging.basicConfig(level=logging.INFO)

def test_spread_inheritance():
    print("Testing Spread Inheritance in BaseBot...")
    
    # Initialize a bot
    bot = MomentumBot(name="test-momentum", generation=0)
    
    # Mock market (missing spread keys)
    market = {
        "current_price": 0.5,
        "question": "Bitcoin Up or Down Test",
        "id": "test-id"
    }
    
    # Mock signals with orderflow spread
    signals = {
        "prices": [0.5, 0.5, 0.5],
        "orderflow": {
            "spread_pct": 1.25
        }
    }
    
    # Force a skip by setting a high min_ev
    # We want to trigger the skip log block in make_decision
    original_min_ev = config.AGGRESSION_THRESHOLDS["medium"]["min_edge_after_fees"]
    config.AGGRESSION_THRESHOLDS["medium"]["min_edge_after_fees"] = 10.0 # Force skip
    
    try:
        print("Executing make_decision (should log spread_pct=1.25%)...")
        bot.make_decision(market, signals)
    finally:
        # Restore config
        config.AGGRESSION_THRESHOLDS["medium"]["min_edge_after_fees"] = original_min_ev

if __name__ == "__main__":
    test_spread_inheritance()
