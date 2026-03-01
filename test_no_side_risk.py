import sys
from pathlib import Path

# Add root to path
sys.path.insert(0, str(Path(__file__).parent.resolve()))

from core.risk_manager import ArenaRiskManager
from core.position import OpenPosition

def test_no_side_logic():
    print("Testing RiskManager Logic for 'NO' Side...")
    rm = ArenaRiskManager()
    
    # --- Scenario 1: Fixed SL/TP for NO Side ---
    # YES Price = 0.70 -> NO Price = 0.30
    entry_price = 0.30 
    sl_pct = -0.10 # 10% Loss
    tp_pct = 0.20  # 20% Profit
    
    sl_tp = rm.calculate_sl_tp(fill_price=entry_price, enable_sl_tp=True, sl_pct=sl_pct, tp_pct=tp_pct, side='no')
    print(f"Calculated SL/TP (YES terms) for NO entry=0.30: {sl_tp}")
    # NO Entry 0.30 -> YES 0.70. SL (10%) = 0.77. TP (20%) = 0.56.
    
    import time
    pos = OpenPosition(
        trade_id="test_no",
        bot_name="tester",
        market_id="m1",
        direction="NO",
        # In current DB, entry_price is the TOKEN price.
        entry_price=entry_price, # 0.30
        size_usd=100.0,
        entry_time=time.time(),
        shares=100.0 / entry_price,
        sl_price=sl_tp['sl_price'], # 0.77
        tp_price=sl_tp['tp_price']  # 0.56
    )
    rm.open_positions[pos.trade_id] = pos
    
    # 1. Test Loss (YES price goes UP to 0.80 -> NO bet LOSES)
    print("\nTesting Loss (YES goes to 0.80):")
    market_prices = {"m1": {"current_price": 0.80}}
    exits = rm.check_sl_tp(market_prices)
    if exits:
        for p, reason, price in exits:
            print(f"EXIT TRIGGERED: {reason} at YES price {price:.3f}")
            # Mocking db.resolve_trade to avoid sqlite issues in test
            # Capture the pnl calculated in close_position manually for inspection
            entry_yes = 1.0 - p.entry_price
            pnl = (entry_yes - price) * p.shares
            print(f"PnL Calculated: ${pnl:.2f} (Expected Negative)")
    else:
        print("NO EXIT TRIGGERED (Unexpected - should be SL)")
        
    # 2. Test Profit (YES price goes DOWN to 0.50 -> NO bet WINS)
    print("\nTesting Profit (YES goes to 0.50):")
    market_prices = {"m1": {"current_price": 0.50}}
    exits = rm.check_sl_tp(market_prices)
    if exits:
        for p, reason, price in exits:
            print(f"EXIT TRIGGERED: {reason} at YES price {price:.3f}")
            entry_yes = 1.0 - p.entry_price
            pnl = (entry_yes - price) * p.shares
            print(f"PnL Calculated: ${pnl:.2f} (Expected Positive)")
    else:
        print("NO EXIT TRIGGERED (Unexpected - should be TP)")

    # --- Scenario 2: Trailing TP for NO Side ---
    print("\nTesting Trailing TP for NO Side...")
    pos.trailing_enabled = True
    pos.trailing_distance = 0.05
    pos.tp_price = 0.25 # Initial TP floor
    
    # Price goes up to 0.40 (NO price)
    print("Price goes to 0.40 (NO):")
    rm.update_trailing_tp(pos, 0.40)
    print(f"New Trailing TP (should be 0.35): {pos.tp_price:.3f}")
    
    # Price drops to 0.34 (NO price)
    print("Price drops to 0.34 (NO):")
    market_prices = {"m1": {"current_price": 1.0 - 0.34}}
    exits = rm.check_sl_tp(market_prices)
    if exits:
        for p, reason, price in exits:
            print(f"EXIT TRIGGERED: {reason} at price {price:.3f}")

if __name__ == "__main__":
    test_no_side_logic()
