
import sys
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

# Mock dependencies before importing ArenaRiskManager
sys.modules['telegram_notifier'] = MagicMock()
sys.modules['polymarket_client'] = MagicMock()

# Import the classes we want to test
from core.risk_manager import ArenaRiskManager
from core.position import OpenPosition

class TestDailyPnLLimit(unittest.TestCase):
    def setUp(self):
        self.rm = ArenaRiskManager()
        self.rm.mode = "paper"
        self.rm.bankroll = 10000.0
        self.rm.limits = {
            "max_daily_loss_global": 1500.0, # 15% of 10k
            "max_daily_loss_per_bot": 1500.0,
            "max_pos_per_bot": 500.0,
            "max_global_position": 2500.0
        }

    @patch('db.get_daily_net_pnl')
    @patch('db.get_total_open_position_value')
    @patch('db.get_total_open_position_value_all_bots')
    def test_can_place_trade_respects_net_pnl(self, mock_total_global, mock_total_bot, mock_net_pnl):
        # Case 1: Realized Net PnL is positive ($500 profit)
        mock_net_pnl.return_value = 500.0
        mock_total_bot.return_value = 0.0
        mock_total_global.return_value = 0.0
        allowed, reason = self.rm.can_place_trade("bot1", 10.0)
        self.assertTrue(allowed)
        
        # Case 2: Realized Net PnL is negative ($1000 loss), but below limit ($1500)
        mock_net_pnl.return_value = -1000.0
        allowed, reason = self.rm.can_place_trade("bot1", 10.0)
        self.assertTrue(allowed)
        
        # Case 3: Realized Net PnL breaches 15% limit ($1600 loss)
        mock_net_pnl.return_value = -1600.0
        allowed, reason = self.rm.can_place_trade("bot1", 10.0)
        self.assertFalse(allowed)
        self.assertEqual(reason, "daily_loss_global")

    def test_get_floating_pnl(self):
        # Setup open positions
        pos_yes = OpenPosition(
            market_id="m1", bot_name="b1", direction="yes", 
            entry_price=0.50, size_usd=100.0, entry_time=0, 
            shares=200.0, trade_id="t1"
        )
        pos_no = OpenPosition(
            market_id="m2", bot_name="b1", direction="no", 
            entry_price=0.40, size_usd=100.0, entry_time=0, 
            shares=250.0, trade_id="t2"
        )
        
        self.rm.open_positions = {"t1": pos_yes, "t2": pos_no}
        
        # Market prices
        # m1 (YES position): Entry 0.50 -> Now 0.60 (Profit: 0.10 * 200 = +20)
        # m2 (NO position): Entry 0.40 (YES side was 0.60) -> Now 0.70 (Loss: 0.60 - 0.70 = -0.10 * 250 = -25)
        market_prices = {
            "m1": {"current_price": 0.60},
            "m2": {"current_price": 0.70}
        }
        
        floating = self.rm.get_floating_pnl(market_prices)
        self.assertAlmostEqual(floating, -5.0, places=5) # +20 - 25 = -5

if __name__ == '__main__':
    unittest.main()
