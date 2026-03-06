import unittest
from unittest.mock import patch, MagicMock
from core.position import OpenPosition
import core.risk_manager as risk_manager
import config

class TestIsolationPatch(unittest.TestCase):
    def setUp(self):
        # Mocks
        config.get_current_mode = MagicMock(return_value="live")
        self.rm = risk_manager.ArenaRiskManager()
        self.rm.mode = "live"

    @patch('polymarket_client.cancel_all_market_orders')
    @patch('polymarket_client.get_token_balance')
    @patch('polymarket_client.place_market_order')
    @patch('db.resolve_trade')
    def test_synchronized_sell_exact_balance(self, mock_resolve, mock_place, mock_balance, mock_cancel):
        # Setup pos
        pos = OpenPosition(
            market_id="m1", bot_name="botA", direction="yes", entry_price=0.5,
            size_usd=100, shares=200.0, owner_tag="copy_0xABC", trade_id="t1", id=1, token_id="tok1", entry_time=1.0
        )
        self.rm.open_positions["t1"] = pos
        
        # Mock responses
        mock_balance.return_value = 500.0 # Broker balance is larger due to other bots
        mock_place.return_value = {"success": True, "price": 0.6}
        
        # Execute
        self.rm.close_position(pos, "SL", 0.6)
        
        # Verify 
        mock_cancel.assert_called_once_with("m1")
        mock_balance.assert_called_once_with("tok1")
        # Should only sell 'pos.shares' (200.0), not the full 500
        mock_place.assert_called_once_with(token_id="tok1", side="sell", amount=200.0)
        self.assertNotIn("t1", self.rm.open_positions)
        print("✅ Correctly isolated position sell when balance > shares.")

    @patch('polymarket_client.cancel_all_market_orders')
    @patch('polymarket_client.get_token_balance')
    @patch('polymarket_client.place_market_order')
    @patch('db.resolve_trade')
    def test_synchronized_sell_short_balance(self, mock_resolve, mock_place, mock_balance, mock_cancel):
        # Setup pos
        pos = OpenPosition(
            market_id="m1", bot_name="botA", direction="yes", entry_price=0.5,
            size_usd=100, shares=200.0, owner_tag="botA", trade_id="t2", id=2, token_id="tok1", entry_time=1.0
        )
        self.rm.open_positions["t2"] = pos
        
        # Mock responses
        mock_balance.return_value = 150.0 # Broker has less than tracked
        mock_place.return_value = {"success": True, "price": 0.6}
        
        # Execute
        self.rm.close_position(pos, "TP", 0.6)
        
        # Verify 
        mock_cancel.assert_called_once_with("m1")
        mock_balance.assert_called_once_with("tok1")
        # Should sell exactly what broker has left (150.0)
        mock_place.assert_called_once_with(token_id="tok1", side="sell", amount=150.0)
        self.assertNotIn("t2", self.rm.open_positions)
        print("✅ Correctly adapted sell size when balance < shares.")

if __name__ == '__main__':
    unittest.main()
