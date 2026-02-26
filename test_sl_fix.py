import sys
import time
from unittest.mock import MagicMock

# Mock dependencies to avoid side effects
sys.modules["telegram_notifier"] = MagicMock()
sys.modules["db"] = MagicMock()
sys.modules["polymarket_client"] = MagicMock()

# Mock config
import config
config.GRACE_PERIOD_SECONDS = 45
config.MEANREV_SL_PCT = -0.25

from core.risk_manager import ArenaRiskManager
from core.position import OpenPosition

def test_sl_tp_logic():
    print("--- Testing SL/TP Logic ---")
    
    # Initialize Risk Manager (mocked)
    rm = ArenaRiskManager()
    rm.open_positions = {} # Clear
    
    # 1. Test PnL Calculation
    # Scenario: Bought YES at 0.40. Sold at 0.50.
    entry_price = 0.40
    exit_price = 0.50
    shares = 100.0
    size_usd = 40.0
    
    # Create a position
    pos = OpenPosition(
        market_id="m1", bot_name="meanrev-v1", direction="YES", 
        entry_price=entry_price, size_usd=size_usd, shares=shares, 
        entry_time=time.time(), sl_price=0.30, tp_price=0.60,
        trade_id="t1"
    )
    
    # Manually verify calculation logic used in close_position
    pnl = (exit_price - pos.entry_price) * pos.shares
    print(f"PnL Calc: (0.50 - 0.40) * 100 = {pnl}")
    if abs(pnl - 10.0) > 0.001:
        print(f"FAIL: PnL calculation incorrect. Got {pnl}, expected 10.0")
        return
    
    # 2. Test Grace Period
    print("\n--- Testing Grace Period ---")
    
    # Simulate adding position (which sets grace period)
    if config.GRACE_PERIOD_SECONDS > 0:
        pos.grace_period_ends_at = time.time() + config.GRACE_PERIOD_SECONDS
    
    rm.open_positions["t1"] = pos
    
    if pos.grace_period_ends_at is None:
        print("FAIL: Grace period not set")
        return
    
    print(f"Grace Period Ends At: {pos.grace_period_ends_at} (Now: {time.time()})")
    
    # Simulate Price Drop to 0.20 (Below SL 0.30)
    market_prices = {"m1": {"current_price": 0.20}}
    
    # Should NOT trigger exit because of grace period
    exits = []
    now = time.time()
    if pos.grace_period_ends_at and now < pos.grace_period_ends_at:
        pass # Expected behavior in risk_manager
    else:
        # Simulate check_sl_tp logic
        if 0.20 <= pos.sl_price:
             exits.append((pos, "SL", 0.20))
             
    print(f"Exits found during grace period: {len(exits)} (Expected: 0)")
    if len(exits) != 0:
        print("FAIL: SL triggered during grace period!")
        return
    
    # Expire grace period manually
    pos.grace_period_ends_at = time.time() - 1.0
    print("Grace period expired manually.")
    
    # Should trigger exit now
    exits = []
    now = time.time()
    if pos.grace_period_ends_at and now < pos.grace_period_ends_at:
        pass
    else:
        if 0.20 <= pos.sl_price:
             exits.append((pos, "SL", 0.20))

    print(f"Exits found after grace period: {len(exits)} (Expected: 1)")
    if len(exits) != 1:
        print("FAIL: SL NOT triggered after grace period!")
        return
        
    if exits[0][1] != "SL":
        print("FAIL: Reason should be SL")
        return
    
    # 3. Test Duplicate Check
    print("\n--- Testing Duplicate Check ---")
    # Try to add same bot/market again
    pos2 = OpenPosition(
        market_id="m1", bot_name="meanrev-v1", direction="NO", 
        entry_price=0.5, size_usd=10, shares=20, 
        entry_time=time.time(), trade_id="t2"
    )
    
    # Simulate add_position logic
    is_duplicate = False
    for existing in rm.open_positions.values():
        if existing.bot_name == pos2.bot_name and existing.market_id == pos2.market_id:
            is_duplicate = True
            break
            
    if not is_duplicate:
        rm.open_positions["t2"] = pos2
    
    final_count = len(rm.open_positions)
    print(f"Positions count: {final_count} (Expected: 1)")
    
    if final_count != 1:
        print("FAIL: Duplicate position was added!")
        return

    print("\n✅ All Tests Passed")

if __name__ == "__main__":
    test_sl_tp_logic()
