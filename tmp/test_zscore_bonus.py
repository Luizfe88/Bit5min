import sys
from pathlib import Path
import math
import unittest
from unittest.mock import MagicMock, patch

# Add root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from bots.base_bot import BaseBot

class MockBot(BaseBot):
    def analyze(self, market, signals):
        return {
            "action": "buy",
            "side": "yes",
            "confidence": 0.5,
            "reasoning": "test"
        }

class TestZScoreBonus(unittest.TestCase):
    def setUp(self):
        self.bot = MockBot("TestCaseBot", "hybrid", {})

    @patch("core.oracle.PriceOracle.get_binance_movement")
    @patch("core.oracle.PriceOracle.apply_bayesian_update")
    @patch("edge_model.predict_yes_probability")
    @patch("db.get_bot_brier_score")
    @patch("db.get_total_current_capital")
    @patch("db.get_total_open_position_value_all_bots")
    @patch("core.risk_manager.risk_manager.can_place_trade")
    def test_z_score_bonus_multiplier(self, mock_can_trade, mock_exposure, mock_capital, mock_brier, mock_predict, mock_update, mock_movement):
        # Setup mocks
        mock_movement.return_value = 0.0
        # p_yes will be 0.7, market_price 0.5 -> diff = 0.2
        # sigma = max(0.005, vol). If vol=0.05, sigma=0.05.
        # z_score = 0.2 / 0.05 = 4.0
        mock_predict.return_value = 0.7 
        mock_update.return_value = 0.7
        mock_brier.return_value = 0.35 # Would have had penalty previously
        mock_capital.return_value = 10000.0
        mock_exposure.return_value = 0.0
        mock_can_trade.return_value = (True, "")

        market = {"current_price": 0.5, "id": "test_mkt", "best_bid": 0.49, "best_ask": 0.51}
        signals = {"prices": [0.5, 0.5, 0.5, 0.5, 0.5, 0.5], "latest": 0.5} 
        
        # sigma = 0.1, diff = 0.2 -> z_score = 2.0
        # log(2.0) * 0.18 = 0.693 * 0.18 = 0.12474
        
        with patch("bots.base_bot.math.sqrt", return_value=0.1): # force vol = 0.1
            decision = self.bot.make_decision(market, signals)
            
        conf = decision["confidence"]
        # Base confidence: abs(0.7 - 0.5) * 2.5 = 0.5
        # Bonus: log(2.0) * 0.18 = 0.12474
        # Expected: 0.5 + 0.12474 = 0.62474 (Even with mock_brier=0.35)
        print(f"Confidence: {conf}, Expected: ~0.62474")
        self.assertAlmostEqual(conf, 0.5 + math.log(2.0) * 0.18, places=4)

    def test_config_min_confidence(self):
        print(f"Config Medium Confidence: {config.get_min_confidence()}")
        self.assertEqual(config.get_min_confidence(), 0.40)

if __name__ == "__main__":
    unittest.main()
