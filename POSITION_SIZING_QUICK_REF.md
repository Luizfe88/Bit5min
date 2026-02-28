# Dynamic Position Sizing — Quick Reference

## Feature Summary

**What:** Positions now size dynamically based on signal confidence + global cap of 50% exposure

**Multipliers:**
- Confidence ≥0.90: 2.8x → ~22.4% position
- Confidence ≥0.80: 2.2x → ~17.6% position  
- Confidence ≥0.70: 1.65x → ~13.2% position
- Confidence ≥0.60: 1.15x → ~9.2% position
- Confidence ≥0.50: 0.65x → ~5.2% position
- Confidence <0.50: 0.35x → ~2.8% position

**Formula:**
```
position_size = min(
    base_size(8%) * multiplier,
    max_allowed_by_global_cap(50%)
)
```

**Minimum:** $5.00 per trade

---

## Expected Log Output

```
[bot-name] Position Sizing: conf=0.87 → multiplier=2.2 → 
  desired=$1,760 → final=$1,760 (total_exposure=29.6% of capital)
```

---

## Where It's Implemented

| File | Method | Purpose |
|------|--------|---------|
| `config.py` | `get_confidence_multiplier()` | Get multiplier for confidence |
| `config.py` | `get_base_position_percent()` | Get 8% base |
| `config.py` | `get_max_total_exposure()` | Get 50% cap |
| `bots/base_bot.py` | `calculate_position_size()` | Main sizing logic |
| `bots/base_bot.py` | `execute()` | Uses sizing before placing trade |
| `core/risk_manager.py` | `get_current_total_exposure()` | Get current open positions total |

---

## How It Works (Step-by-Step)

1. **Bot analyzes market** → generates signal with confidence (0.0-1.0)
2. **execute() method called** → gets current position size sizing
3. **Get current exposure** from database (sum of all open positions)
4. **Call calculate_position_size()** with:
   - confidence (from signal)
   - current_exposure (from DB)
   - total_capital (from config)
5. **Method returns position size** (adjusted for exposure cap)
6. **Check if size ≥ $5** (minimum)
   - If yes: proceed to RiskManager checks
   - If no: skip trade
7. **RiskManager validates** (daily limits, spreads, etc.)
8. **Place trade** with dynamically calculated size

---

## Config Variables

```python
# In config.py, lines ~140-165

BASE_POSITION_PERCENT = 0.08  # 8% of capital as base per trade
MAX_TOTAL_EXPOSURE = 0.50  # 50% global cap
MIN_TRADE_SIZE = 5.0  # Minimum position in dollars

CONFIDENCE_POSITION_MULTIPLIERS = {
    0.90: 2.8,    # Extra aggressive
    0.80: 2.2,    # Very aggressive
    0.70: 1.65,   # Aggressive
    0.60: 1.15,   # Medium
    0.50: 0.65,   # Conservative
    0.00: 0.35,   # Very conservative
}
```

---

## Testing Position Sizing

### Check Multiplier Function
```bash
python3 -c "
import config
confs = [0.95, 0.85, 0.75, 0.65, 0.55, 0.25]
for c in confs:
    mult = config.get_confidence_multiplier(c)
    size_pct = 0.08 * mult * 100
    print(f'conf={c:.2f} → multiplier={mult} → {size_pct:.1f}% of capital')
"
```

### Simulate Sizing
```bash
python3 << 'EOF'
import config
from bots.base_bot import BaseBot  # Mock or import real bot class

class TestBot(BaseBot):
    def analyze(self, market, signals): return {}

bot = TestBot('test', 'test', {})

# High confidence
size1 = bot.calculate_position_size(0.87, 2000, 10000)
print(f'High conf: ${size1}')

# Low confidence  
size2 = bot.calculate_position_size(0.52, 4500, 10000)
print(f'Low conf (capped): ${size2}')

# Very low exposure
size3 = bot.calculate_position_size(0.95, 100, 10000)
print(f'Room to spare: ${size3}')
EOF
```

### Monitor in Real-Time
```bash
# Watch position sizing decisions live
tail -f logs/arena.log.* | grep "Position Sizing"

# Count position sizing rejections
grep "Position too small" logs/arena.log.* | wc -l

# Check current exposure
sqlite3 data/arena.db "SELECT SUM(amount) FROM trades WHERE outcome IS NULL;"
```

---

## Common Scenarios

### Scenario 1: High Confidence, Plenty of Room
```
conf=0.87 (HIGH)
current_exposure = $2,000 (20% of $10k capital)
max_allowed = $5,000 (50% cap)

→ desired_size = 8% * 2.2 = 17.6% = $1,760
→ final_size = min($1,760, $5,000 - $2,000) = $1,760
→ Result: PLACE $1,760 ✅ (total exposure becomes 37.6%)
```

### Scenario 2: Low Confidence, Limited Room
```
conf=0.52 (LOW)
current_exposure = $4,500 (45% of $10k capital)
max_allowed = $5,000 (50% cap)

→ desired_size = 8% * 0.65 = 5.2% = $520
→ final_size = min($520, $5,000 - $4,500) = $500
→ Result: PLACE $500 ✅ (capped by global limit, at 50% total)
```

### Scenario 3: At Global Capacity
```
conf=0.90 (VERY HIGH)
current_exposure = $5,000 (50% of $10k capital - AT CAP)
max_allowed = $5,000 (50% cap)

→ desired_size = 8% * 2.8 = 22.4% = $2,240
→ final_size = min($2,240, $5,000 - $5,000) = $0
→ Result: SKIP TRADE 🚫 (no capacity available)
  [bot-name] Position too small ($0 < min $5.00)
```

### Scenario 4: Low Confidence + Limited Room + Low Exposure
```
conf=0.31 (VERY LOW)
current_exposure = $4,800 (48% of $10k capital)
max_allowed = $5,000 (50% cap)

→ desired_size = 8% * 0.35 = 2.8% = $280
→ final_size = min($280, $5,000 - $4,800) = $200
→ BUT: $200 < $5 minimum
→ Result: SKIP TRADE 🚫
  [bot-name] Position too small ($200 < min $5.00)
```

---

## Adjustment Guide

### More Aggressive (Larger Positions)
```python
# Option 1: Increase base
BASE_POSITION_PERCENT = 0.12  # was 0.08 → +50% larger

# Option 2: Increase multipliers
CONFIDENCE_POSITION_MULTIPLIERS = {
    0.90: 3.5,   # was 2.8
    0.80: 2.7,   # was 2.2
    # ... etc ...
}

# Option 3: Raise global cap
MAX_TOTAL_EXPOSURE = 0.60  # was 0.50 → 60% instead of 50%
```

### More Conservative (Smaller Positions)
```python
# Option 1: Decrease base
BASE_POSITION_PERCENT = 0.04  # was 0.08 → -50% smaller

# Option 2: Decrease multipliers
CONFIDENCE_POSITION_MULTIPLIERS = {
    0.90: 1.4,   # was 2.8 → half
    0.80: 1.1,   # was 2.2 → half
    # ... etc ...
}

# Option 3: Lower global cap
MAX_TOTAL_EXPOSURE = 0.40  # was 0.50 → 40% instead of 50%
```

---

## Troubleshooting

| Issue | Check | Solution |
|-------|-------|----------|
| All trades rejected as "position_too_small" | Signal confidences in logs | Lower MIN_TRADE_SIZE or increase BASE_POSITION_PERCENT |
| Positions always capped at $500-1000 | Current exposure % | Close some positions or raise MAX_TOTAL_EXPOSURE |
| Some bots never trade with 2.8x multiplier | Confidence values | Review signal calculation, check if min_confidence floor is blocking |
| Position sizing not working | Check if execute() uses new logic | Verify base_bot.py has calculate_position_size() call |

---

## Performance Impacts

**Expected improvements:**
- ✅ Better risk-adjusted returns (size matched to signal quality)
- ✅ Lower drawdown (automatic cap prevents over-leverage)
- ✅ More consistent position sizing (less "all-in" trading)
- ✅ Better evolution selection (high-confidence win rate matters more)

**Potential drawbacks:**
- ⚠️ Lower average position size if signals are low-confidence
- ⚠️ More trades rejected at low confidence (good for safety, bad for action)

---

## Key Files Modified

```
✅ config.py
   - Added BASE_POSITION_PERCENT, MAX_TOTAL_EXPOSURE, MIN_TRADE_SIZE
   - Added CONFIDENCE_POSITION_MULTIPLIERS dict
   - Added getter functions

✅ bots/base_bot.py
   - Added calculate_position_size() method (core logic)
   - Updated execute() to call new sizing method
   - Added position_too_small rejection

✅ core/risk_manager.py
   - Added get_current_total_exposure() method
   - Added get_current_exposure_percent() method
```

---

## Next: Monitor & Adjust

After enabling:
1. **Watch logs** for position sizing patterns
2. **Track metrics:** avg position size, global exposure %, WR by confidence tier
3. **After 100 trades:** evaluate if sizing is helping or hurting
4. **Adjust multipliers** if needed (more or less aggressive)
5. **Monitor P&L** to see if risk-adjusted returns improved

---

## Support

For detailed info: See `DYNAMIC_POSITION_SIZING.md`
For quick logs: `tail -f logs/arena.log.* | grep "Position Sizing"`
