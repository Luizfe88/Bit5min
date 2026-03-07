import sys
import os
from pathlib import Path

# Mock config and db before importing risk_manager
import types
config = types.ModuleType('config')
config.ENABLE_SL_TP_PER_BOT = {}
config.MEANREV_SL_PCT = 0.25
config.MEANREV_TP_PCT = 0.18
config.MAX_POSITION_PCT_OF_BALANCE = 0.02
config.MAX_TOTAL_POSITION_PCT_OF_BALANCE = 0.50
config.MAX_LOSS_PCT_PER_BOT = 0.15
config.MAX_LOSS_PCT_TOTAL = 0.50
config.get_current_mode = lambda: 'paper'
config.get_min_trade_amount = lambda: 1.0

sys.modules['config'] = config

db = types.ModuleType('db')
db.get_total_current_capital = lambda mode: 10000.0
db.get_total_open_position_value_all_bots = lambda mode: 0.0
db.get_bot_daily_net_pnl = lambda bot, mode: 0.0
db.get_daily_net_pnl = lambda mode: 0.0
db.get_total_open_position_value = lambda bot, mode: 0.0
db.update_position_sl_tp = lambda **kwargs: None
sys.modules['db'] = db

telegram_notifier = types.ModuleType('telegram_notifier')
telegram_notifier.get_telegram_notifier = lambda: None
sys.modules['telegram_notifier'] = telegram_notifier

sys.path.insert(0, str(Path(os.getcwd())))

from core.risk_manager import ArenaRiskManager
from core.position import OpenPosition

def test_no_side_sl_tp():
    rm = ArenaRiskManager()
    
    # Contexto do bug: Entry NO @0.604 (YES @0.396), TP 25% (exemplo)
    # se TP=5%, entry=0.604 -> token_tp = 0.604 * 1.05 = 0.6342
    # yes_tp_trigger = 1 - 0.6342 = 0.3658
    
    fill_price = 0.604
    sl_pct = 0.05
    tp_pct = 0.05
    side = 'no'
    
    res = rm.calculate_sl_tp(fill_price, True, sl_pct, tp_pct, side)
    print(f"NO Entry @{fill_price} (Token NO)")
    print(f"Calculated SL Price (YES trigger): {res['sl_price']:.4f}")
    print(f"Calculated TP Price (YES trigger): {res['tp_price']:.4f}")
    
    # Expectation:
    # entry_yes = 1 - 0.604 = 0.396
    # TP Token NO = 0.604 * 1.05 = 0.6342 -> YES trigger = 1 - 0.6342 = 0.3658
    # SL Token NO = 0.604 * 0.95 = 0.5738 -> YES trigger = 1 - 0.5738 = 0.4262
    
    assert abs(res['tp_price'] - 0.3658) < 0.001, f"TP Price mismatch: {res['tp_price']}"
    assert abs(res['sl_price'] - 0.4262) < 0.001, f"SL Price mismatch: {res['sl_price']}"
    print("SUCCESS: calculate_sl_tp for NO side is CORRECT!")

def test_profit_guard():
    rm = ArenaRiskManager()
    
    # Bug Evidence: 
    # [ENTRY] momentum-g13-283 no @0.604
    # TP TRIGGERED (NO) momentum-g13-283 at YES=0.4037.
    # entry_yes = 1 - 0.604 = 0.396. 
    # Current YES = 0.4037 -> Position is in LOSS (YES rose, so NO fell).
    
    pos = OpenPosition(
        market_id="m1",
        bot_name="bot1",
        direction="no",
        entry_price=0.604, # Token Price
        size_usd=100,
        entry_time=1000,
        sl_price=0.4262,
        tp_price=0.4037, # Simulated TP price that was trigger erroneously
        trade_id="t1",
        shares=165 # dummy
    )
    rm.open_positions["t1"] = pos
    
    market_prices = {"m1": {"current_price": 0.4037}}
    
    exits = rm.check_sl_tp(market_prices)
    
    # Should NOT exit because of Profit Guard (0.4037 > 1 - 0.604)
    # 0.4037 is > 0.396, so YES rose, NO position is LOSING.
    
    for p, reason, price in exits:
        if "TP" in reason:
            print(f"BUG PERSISTS: TP triggered at {price} while in loss!")
            return
            
    print("SUCCESS: Profit Guard successful: TP NOT triggered for losing NO position.")

if __name__ == "__main__":
    try:
        test_no_side_sl_tp()
        test_profit_guard()
        print("\nALL TESTS PASSED!")
    except Exception as e:
        print(f"\nTEST FAILED: {e}")
        sys.exit(1)
