"""
Test: Ratchet Catraca (Trailing Stop Lock-in)

Scenario A (YES): TP hit at 0.500, market crashes to 0.350.
  - Without fix: SL was 0.020 below entry => exits in loss.
  - With fix: SL immediately locked at entry + 80% of gain => exits in profit.

Scenario B (NO): TP hit at YES=0.332, market bounces to YES=0.580.
  - Without fix: SL ceiling was 0.020 above entry => exits in loss.
  - With fix: SL ceiling locked at entry_yes - 80% of drop => exits in profit.

Scenario C: Ratchet only moves in the profitable direction.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.risk_manager import ArenaRiskManager
from core.position import OpenPosition
import time

rm = ArenaRiskManager()

def mkpos(direction, entry_token, sl, tp):
    p = OpenPosition(
        trade_id="t_%s_%s" % (direction, time.time()),
        bot_name="tester",
        market_id="m1",
        direction=direction,
        entry_price=entry_token,
        size_usd=920.0,
        entry_time=time.time(),
        shares=920.0 / entry_token,
        sl_price=sl,
        tp_price=tp,
    )
    rm.open_positions[p.trade_id] = p
    return p

SL, TP = 0.25, 0.18

# ─── Scenario A: YES Lock-in ───────────────────────────────────────────────
print("=== Scenario A: YES Lock-in ===")
# Entry 0.40, YES TP = 0.40 * 1.18 = 0.472
r = rm.calculate_sl_tp(0.40, True, SL, TP, side='yes')
print("  YES: sl=%.4f  tp=%.4f" % (r['sl_price'], r['tp_price']))
p = mkpos("YES", 0.40, r['sl_price'], r['tp_price'])

# Tick 1: TP hit at 0.480 (above TP 0.472)
e = rm.check_sl_tp({"m1": {"current_price": 0.480}})
assert p.tp_triggered, "YES tp_triggered should be True after 0.480"
assert p.sl_price > 0.40, "YES lock-in SL must be ABOVE entry (got %.4f)" % p.sl_price
print("  A1: TP hit. Lock-in SL=%.4f  entry=0.40  (SL > entry: %s)" % (p.sl_price, p.sl_price > 0.40))

# Tick 2: Market crashes to 0.350 (below lock-in SL)
e = rm.check_sl_tp({"m1": {"current_price": 0.350}})
exited = any(x.trade_id == p.trade_id for x, *_ in e)
assert exited, "YES should have exited at Trailing SL on crash to 0.350"
# PnL: exit is at SL, not at 0.350 directly. The label should say Trailing Exit.
label = next((lbl for x, lbl, _ in e if x.trade_id == p.trade_id), "?")
print("  A2: Exited with label='%s'" % label)
assert "Trailing" in label, "Expected Trailing Exit label, got: %s" % label
rm.open_positions.pop(p.trade_id, None)
print("  PASS: YES crash protected by lock-in SL > entry")

# ─── Scenario B: NO ────────────────────────────────────────────────────────
print("\n=== Scenario B: NO Lock-in ===")
r2 = rm.calculate_sl_tp(0.434, True, SL, TP, side='no')
NO_SL, NO_TP = r2['sl_price'], r2['tp_price']
p2 = mkpos("NO", 0.434, NO_SL, NO_TP)
entry_yes_no = 1.0 - 0.434  # 0.566

# Tick 1: YES falls to 0.20 (big profit for NO)
e = rm.check_sl_tp({"m1": {"current_price": 0.20}})
assert p2.tp_triggered, "NO tp_triggered should be True"
assert p2.sl_price < entry_yes_no, "NO lock-in SL must be BELOW entry_yes (SL=%.4f entry_yes=%.4f)" % (p2.sl_price, entry_yes_no)
print("  B1: TP hit. Lock-in SL=%.4f  entry_yes=%.4f  (SL < entry_yes: %s)" % (p2.sl_price, entry_yes_no, p2.sl_price < entry_yes_no))

# Tick 2: YES bounces hard to 0.580 (above lock-in SL ceiling)
sl_after_lock = p2.sl_price
e = rm.check_sl_tp({"m1": {"current_price": 0.580}})
exited2 = any(x.trade_id == p2.trade_id for x, *_ in e)
assert exited2, "NO should have exited at SL on bounce to 0.580"
label2 = next((lbl for x, lbl, _ in e if x.trade_id == p2.trade_id), "?")
print("  B2: Exited with label='%s'" % label2)
rm.open_positions.pop(p2.trade_id, None)
print("  PASS: NO lock-in correctly left SL below entry_yes (%.4f < %.4f)" % (sl_after_lock, entry_yes_no))

# ─── Scenario C: Ratchet direction ─────────────────────────────────────────
print("\n=== Scenario C: Ratchet direction ===")
r3 = rm.calculate_sl_tp(0.400, True, SL, TP, side='yes')
p3 = mkpos("YES", 0.400, r3['sl_price'], r3['tp_price'])

# Trigger TP
rm.check_sl_tp({"m1": {"current_price": 0.480}})
sl_after_tp = p3.sl_price
print("  C1: After TP trigger at 0.480, SL=%.4f" % sl_after_tp)

# Price rises further: SL should ratchet up
rm.check_sl_tp({"m1": {"current_price": 0.520}})
sl_after_rise = p3.sl_price
assert sl_after_rise >= sl_after_tp, "Ratchet: SL must not decrease on rise"
print("  C2: After rise to 0.520, SL=%.4f  (non-decreasing: %s)" % (sl_after_rise, sl_after_rise >= sl_after_tp))

# Price drops: SL must NOT decrease (catraca)
rm.check_sl_tp({"m1": {"current_price": 0.490}})
sl_after_drop = p3.sl_price
assert sl_after_drop >= sl_after_rise, "CATRACA FAILED: SL decreased on drop!"
print("  C3: After drop to 0.490, SL=%.4f  (held: %s)" % (sl_after_drop, sl_after_drop >= sl_after_rise))
rm.open_positions.pop(p3.trade_id, None)
print("  PASS: Ratchet correctly only moves in profit direction")

print("\nALL RATCHET TESTS PASSED")
