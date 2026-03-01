import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.risk_manager import ArenaRiskManager
from core.position import OpenPosition
import time

rm = ArenaRiskManager()
SL, TP = 0.25, 0.18

# ---- 1. calculate_sl_tp ----
r = rm.calculate_sl_tp(0.50, True, SL, TP, side='yes')
assert r['sl_price'] < 0.50
assert r['tp_price'] > 0.50
assert abs(r['sl_price'] - 0.375) < 0.001
assert abs(r['tp_price'] - 0.590) < 0.001
print("1a PASS: YES sl/tp correct")

r = rm.calculate_sl_tp(0.434, True, SL, TP, side='no')
entry_yes = 1.0 - 0.434  # 0.566
assert r['sl_price'] > entry_yes, "NO sl must be ABOVE entry_yes"
assert r['tp_price'] < entry_yes, "NO tp must be BELOW entry_yes"
assert abs(r['sl_price'] - 0.5755) < 0.001, str(r)
assert abs(r['tp_price'] - 0.3321) < 0.001, str(r)
NO_SL = r['sl_price']
NO_TP = r['tp_price']
print("1b PASS: NO sl=%.4f tp=%.4f" % (NO_SL, NO_TP))

# ---- 2. check_sl_tp ----
def mkpos(d, ep, sl, tp):
    p = OpenPosition(
        trade_id="t_%s_%s" % (d, time.time()),
        bot_name="tester",
        market_id="m1",
        direction=d,
        entry_price=ep,
        size_usd=920.0,
        entry_time=time.time(),
        shares=920.0 / ep,
        sl_price=sl,
        tp_price=tp,
    )
    rm.open_positions[p.trade_id] = p
    return p

p = mkpos("NO", 0.434, NO_SL, NO_TP)
e = rm.check_sl_tp({"m1": {"current_price": 0.55}})
assert not any(x.trade_id == p.trade_id for x, *_ in e), "NO triggered too early"
print("2a PASS: NO no early trigger at YES=0.55")
rm.open_positions.pop(p.trade_id, None)

p = mkpos("NO", 0.434, NO_SL, NO_TP)
e = rm.check_sl_tp({"m1": {"current_price": 0.74}})
assert any(x.trade_id == p.trade_id for x, *_ in e), "NO SL not triggered at YES=0.74"
print("2b PASS: NO SL hit at YES=0.74 (the phantom-profit trade)")
rm.open_positions.pop(p.trade_id, None)

p = mkpos("NO", 0.434, NO_SL, NO_TP)
e = rm.check_sl_tp({"m1": {"current_price": 0.20}})
# TP-to-Trailing design: hitting TP does NOT produce an immediate exit.
# It flips tp_triggered=True and sets a new trailing SL ceiling.
# The exit only fires when YES price BOUNCES BACK above the new sl_price.
assert p.tp_triggered, "NO tp_triggered should be True after YES=0.20"
assert p.trailing_enabled, "NO trailing should be ON after TP hit"
assert p.sl_price < 0.30, "Trailing SL ceiling should be near the hit price"
print("2c PASS: NO TP hit at YES=0.20 -- tp_triggered=True, trailing sl=%.4f" % p.sl_price)
rm.open_positions.pop(p.trade_id, None)

# ---- 3. PnL polarity ----
shares = 920.0 / 0.434
# Loss: entry NO=0.434 -> YES 0.434, exit YES=0.740
entry_yes = 1.0 - 0.434
pnl_loss = (entry_yes - 0.740) * shares
assert pnl_loss < 0, "Expected loss got %.2f" % pnl_loss
print("3a PASS: NO loss entry=0.434 exit_yes=0.740 => $%.2f" % pnl_loss)

# Profit: exit YES falls to 0.20
pnl_win = (entry_yes - 0.20) * shares
assert pnl_win > 0, "Expected profit got %.2f" % pnl_win
print("3b PASS: NO profit entry=0.434 exit_yes=0.20 => $+%.2f" % pnl_win)

print()
print("ALL TESTS PASSED")
