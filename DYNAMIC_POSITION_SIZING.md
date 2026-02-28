# Dynamic Confidence-Based Position Sizing with Global Exposure Cap

**Implemented: February 28, 2026**

This document describes the new **Dynamic Confidence-Based Position Sizing (DCPS)** system that sizes trade positions automatically based on signal confidence while enforcing a strict global exposure cap.

---

## Overview

### Problem Solved
- **Before:** Bots used fixed position sizes regardless of signal quality
- **After:** Position sizes scale dynamically with confidence:
  - High confidence (0.90+) → 2.8x multiplier → ~23% per position
  - Medium confidence (0.60) → 1.15x multiplier → ~9% per position
  - Low confidence (0.50) → 0.65x multiplier → ~5% per position

- **Global Protection:** Never exceed 50% of total capital in open positions across all bots

### Expected Behavior
```
🔥 High Confidence Trade (conf=0.87)
├─ multiplier=2.2 (0.80-0.90 tier)
├─ desired_size = 8% * 2.2 = 17.6% of capital
├─ current_exposure = 12% 
├─ max_allowed = 50% - 12% = 38%
├─ final_size = min(17.6%, 38%) = 17.6% = $1,760
└─ total_exposure_after = 12% + 17.6% = 29.6% ✅

😐 Low Confidence Trade (conf=0.52)
├─ multiplier=0.65 (0.50-0.60 tier)
├─ desired_size = 8% * 0.65 = 5.2% of capital
├─ current_exposure = 45%
├─ max_allowed = 50% - 45% = 5%
├─ final_size = min(5.2%, 5%) = $500
└─ total_exposure_after = 45% + 5% = 50% (at cap) ✅
```

---

## Configuration

### New Config Variables (in `config.py`)

```python
# ===== DYNAMIC CONFIDENCE-BASED POSITION SIZING =====
BASE_POSITION_PERCENT = 0.08  # 8% of total capital as base per trade

MAX_TOTAL_EXPOSURE = 0.50  # 50% global cap on open positions

MIN_TRADE_SIZE = 5.0  # Minimum position size in dollars ($5)

CONFIDENCE_POSITION_MULTIPLIERS = {
    0.90: 2.8,    # Ultra aggressive: 8% * 2.8 = 22.4%
    0.80: 2.2,    # Very aggressive: 8% * 2.2 = 17.6%
    0.70: 1.65,   # Aggressive: 8% * 1.65 = 13.2%
    0.60: 1.15,   # Medium: 8% * 1.15 = 9.2%
    0.50: 0.65,   # Conservative: 8% * 0.65 = 5.2%
    0.00: 0.35,   # Very conservative: 8% * 0.35 = 2.8%
}
```

### New Config Getter Functions

```python
get_confidence_multiplier(confidence: float) -> float
    # Returns multiplier for given confidence level
    
get_base_position_percent() -> float
    # Returns 0.08 (8%)
    
get_max_total_exposure() -> float
    # Returns 0.50 (50%)
    
get_min_trade_size() -> float
    # Returns 5.0 ($5)
```

---

## Implementation Details

### 1. New Method in `BaseBot`

**Location:** `bots/base_bot.py`, method `calculate_position_size()`

```python
def calculate_position_size(
    self, confidence: float, current_exposure: float, total_capital: float
) -> float:
    """
    Calculate dynamic position size based on confidence level with global exposure cap.
    
    Args:
        confidence: Signal confidence (0.0-1.0)
        current_exposure: Current total open position value across all bots ($)
        total_capital: Total available capital ($)
    
    Returns:
        Position size in dollars (min $5, max 50% of capital)
    """
```

**Logic Flow:**
1. Get confidence-based multiplier from tiers
2. Calculate desired_size = base_size (8%) * multiplier
3. Get max_allowed = 50% of capital - current_exposure
4. Apply cap: final_size = min(desired_size, max_allowed)
5. Enforce minimum: final_size = max(final_size, $5)
6. Return rounded to 2 decimals

### 2. Updated `execute()` Method

**Location:** `bots/base_bot.py`, method `execute()`

**Changes:**
- Now calls `calculate_position_size()` instead of using fixed sizing
- Gets current total exposure from database
- Logs detailed position sizing decisions
- Skips trade if size falls below minimum ($5)

**Before:**
```python
amount = min(signal.get("suggested_amount", max_pos * 0.5), max_pos)
```

**After:**
```python
# Get current total exposure and calculate dynamic size
current_exposure = db.get_total_open_position_value_all_bots(mode)
total_capital = config.PAPER_STARTING_BALANCE or 10000.0

# Calculate position size based on confidence
amount = self.calculate_position_size(conf, current_exposure, total_capital)

# Skip if below minimum
if amount < config.get_min_trade_size():
    logger.info(f"[{self.name}] Position too small (${amount:.2f} < min ${config.get_min_trade_size():.2f})")
    return {"success": False, "reason": "position_below_minimum"}
```

### 3. Risk Manager Enhancements

**Location:** `core/risk_manager.py`

New methods:
```python
def get_current_total_exposure() -> float:
    """Returns current total exposure across all open positions"""
    return db.get_total_open_position_value_all_bots(self.mode)

def get_current_exposure_percent(total_capital: float) -> float:
    """Returns current exposure as percentage of capital (0.0-1.0)"""
```

---

## Expected Log Output

### High Confidence Trade Example
```
[updown-g3-184] Position Sizing: conf=0.87 → multiplier=2.2 → 
  desired=$1,760 → final=$1,760 (total_exposure=29.6% of capital)
[updown-g3-184] Agressivo: size=$1,760, confidence=0.87 (87%)
```

### Low Confidence Trade Example
```
[hybrid-g2-45] Position Sizing: conf=0.52 → multiplier=0.65 → 
  desired=$520 → final=$500 (total_exposure=50.0% of capital)
[hybrid-g2-45] Conservador: size=$500, confidence=0.52 (52%), ⚠️ capped by global exposure
```

### Rejected Trade (Too Small)
```
[momentum-g1-88] Position Sizing: conf=0.31 → multiplier=0.35 → 
  desired=$280 → final=$280 (total_exposure=would be 48.2% of capital)
[momentum-g1-88] Position too small ($280 < min $5.00). Skipping.
```

### Global Cap Enforcement
```
[sentiment-g1-52] current_exposure=48.5%, max_allowed=1.5%
[sentiment-g1-52] Posição limitada: desired=$1,000 → final=$150 (capped by global exposure limit)
```

---

## Confidence Tier Explanation

Position multipliers are assigned based on confidence **buckets**, not exact values:

| Confidence Range | Multiplier | Final Position Size | Use Case |
|------------------|-----------|---------------------|----------|
| 0.90-1.00 | 2.8 | ~22.4% of capital | Ultra high confidence signals only |
| 0.80-0.89 | 2.2 | ~17.6% of capital | Very strong signals |
| 0.70-0.79 | 1.65 | ~13.2% of capital | Confident signals |
| 0.60-0.69 | 1.15 | ~9.2% of capital | Moderate confidence |
| 0.50-0.59 | 0.65 | ~5.2% of capital | Low-moderate confidence |
| 0.00-0.49 | 0.35 | ~2.8% of capital | Very low confidence |

### Example Tier Selection

```python
# Confidence 0.87 matches:
# 0.90? No (0.87 < 0.90)
# 0.80? Yes (0.87 >= 0.80) ← Selected
# Returns: multiplier = 2.2

# Confidence 0.65 matches:
# 0.90? No
# 0.80? No  
# 0.70? No
# 0.60? Yes (0.65 >= 0.60) ← Selected
# Returns: multiplier = 1.15

# Confidence 0.22 matches:
# All tier thresholds are too high
# 0.00? Yes (0.22 >= 0.00) ← Default
# Returns: multiplier = 0.35
```

---

## Global Exposure Cap (50%)

### How It Works

The system enforces a **hard cap** of 50% of total capital in open positions:

```
Max Allowed Exposure = 50% * Total Capital
Current Exposure = Sum of all open position amounts

Remaining Capacity = Max Allowed - Current Exposure

If desired_position > Remaining Capacity:
    Final Position = Remaining Capacity
```

### Examples with $10,000 Capital

```
Scenario 1: Plenty of Headroom
├─ Current exposure: $2,000 (20%)
├─ Desired position: $1,760 (17.6%)
├─ Max allowed: $5,000 (50%)
├─ Remaining: $3,000 (30%)
└─ Decision: Can trade at $1,760 ✅

Scenario 2: Limited Headroom
├─ Current exposure: $4,500 (45%)
├─ Desired position: $1,760 (17.6%)
├─ Max allowed: $5,000 (50%)
├─ Remaining: $500 (5%)
└─ Decision: Can only trade $500 (capped) ⚠️

Scenario 3: At Capacity
├─ Current exposure: $5,000 (50%)
├─ Desired position: $1,760 (17.6%)
├─ Max allowed: $5,000 (50%)
├─ Remaining: $0 (0%)
└─ Decision: Skip trade (no capacity) 🚫
```

---

## Risk Safety Layers

Position sizing respects **all existing RiskManager safeguards**:

1. **Daily Loss Limit** — Pauses bot if daily loss > limit
2. **Max Position Per Bot** — 15% of capital per bot
3. **Min Trade Amount** — Won't trade <$0.01
4. **Duplicate Prevention** — No 2x same (bot, market) pair
5. **Spread Filter** — Rejects markets with spread > 5%
6. **Price Sanity** — Won't bet against extreme prices (>0.82 or <0.18)

**Position Sizing is applied BEFORE these checks**, so the size is already calculated when checking limits.

---

## Testing & Validation

### Test 1: Confidence Multiplier Function
```bash
python3 -c "
import config
tests = [(0.95, 2.8), (0.85, 2.2), (0.75, 1.65), (0.65, 1.15), (0.52, 0.65), (0.30, 0.35)]
for conf, expected_mult in tests:
    actual = config.get_confidence_multiplier(conf)
    status = '✓' if actual == expected_mult else '✗'
    print(f'{status} conf={conf:.2f} → multiplier={actual} (expected {expected_mult})')
"
```

### Test 2: Position Sizing Calculation
```bash
python3 -c "
import config
from bots.base_bot import BaseBot

# Mock bot for testing
class TestBot(BaseBot):
    def analyze(self, market, signals): return {}
    
bot = TestBot('test-bot', 'test', {})

# Test: conf=0.87, exposure=$2k, capital=$10k
size = bot.calculate_position_size(confidence=0.87, current_exposure=2000, total_capital=10000)
print(f'Test 1 - High Conf: ${size} (expected ~$1,760)')

# Test: conf=0.52, exposure=$4.5k, capital=$10k  
size = bot.calculate_position_size(confidence=0.52, current_exposure=4500, total_capital=10000)
print(f'Test 2 - Low Conf, Limited Headroom: ${size} (expected ~$500 due to cap)')
"
```

### Test 3: End-to-End Trade Flow
```bash
# 1. Check current exposure
python3 -c "
import db
from core.risk_manager import risk_manager
import config

config.MODE = 'paper'
exposure = db.get_total_open_position_value_all_bots('paper')
percent = (exposure / 10000) * 100
print(f'Current Exposure: \${exposure:.2f} ({percent:.1f}% of \$10k)')
"

# 2. Check position was calculated dynamically
tail -20 logs/arena.log.* | grep "Position Sizing"

# 3. Verify RiskManager still enforces limits
grep "RiskManager denied" logs/arena.log.*
```

---

## Troubleshooting

### Issue: "Position too small" warnings

**Symptom:**
```
[bot-name] Position too small ($280 < min $5.00). Skipping.
```

**Cause:** Confidence is too low + remaining exposure capacity is limited

**Solution:**
- Wait for more capital accumulation (reduce daily losses)
- Or lower MIN_TRADE_SIZE in config (not recommended)

---

### Issue: All trades being capped at same size

**Symptom:**
```
[bot1] final=$500 (total_exposure=50.0% of capital)
[bot2] final=$500 (total_exposure=50.0% of capital)  ← Same size despite different conf
[bot3] final=$500 (total_exposure=50.0% of capital)
```

**Cause:** Global exposure cap is too tight (already at 50%)

**Solution:**
- Check current exposure: `sqlite3 data/arena.db "SELECT SUM(amount) FROM trades WHERE outcome IS NULL"`
- Either close some positions or increase MAX_TOTAL_EXPOSURE in config
- Monitor P&L to recover capital

---

### Issue: Bots never use 2.8x multiplier

**Symptom:**
```
All bots showing multiplier=1.15 or lower, never reaching 2.8x
```

**Cause:** Signals consistently below 0.90 confidence

**Solution:**
- Check actual signal confidences in logs
- Review signal calculation logic in your bot strategies
- Consider if min_confidence floor is preventing high-confidence trades

---

## Performance Metrics to Monitor

After enabling, track these metrics:

| Metric | Monitor For | Healthy Range |
|--------|------------|---|
| Avg Position Size | Position sizing working | Varies by confidence |
| Global Exposure % | Cap effectiveness | 30-50% |
| Position Too Small Rejection Rate | Min size enforcement | <5% of total trades |
| # Times at 50% Cap | Headroom availability | <20% of total trades |
| WR by Confidence Tier | Strategy quality | >50% for high conf |

---

## Configuration for Different Risk Profiles

### Conservative Profile
```python
BASE_POSITION_PERCENT = 0.04  # 4% base (was 8%)
MAX_TOTAL_EXPOSURE = 0.30    # 30% global (was 50%)
MIN_TRADE_SIZE = 10.0
# Multipliers 50% of original
CONFIDENCE_POSITION_MULTIPLIERS = {
    0.90: 1.4,   # was 2.8
    0.80: 1.1,   # was 2.2
    0.70: 0.83,  # was 1.65
    # ... etc ...
}
```

### Aggressive Profile (Current - Recommended for Testing)
```python
BASE_POSITION_PERCENT = 0.08  # 8% base
MAX_TOTAL_EXPOSURE = 0.50     # 50% global
MIN_TRADE_SIZE = 5.0
CONFIDENCE_POSITION_MULTIPLIERS = { ... as configured ... }
```

### Ultra-Aggressive Profile (High Risk)
```python
BASE_POSITION_PERCENT = 0.12  # 12% base
MAX_TOTAL_EXPOSURE = 0.70     # 70% global
MIN_TRADE_SIZE = 1.0
# Multipliers 120% of original
CONFIDENCE_POSITION_MULTIPLIERS = {
    0.90: 3.36,  # was 2.8
    0.80: 2.64,  # was 2.2
    # ... etc ...
}
```

---

## Summary

✅ **Dynamic position sizing now active**
- Position size scales with confidence (high conf → large position)
- Global 50% exposure cap prevents over-leverage
- Automatic enforcement via calculate_position_size()
- All existing risk controls remain intact

📊 **Expected Improvements:**
- Better risk-adjusted returns (larger positions on high-confidence trades)
- Reduced drawdown risk (automatic capping)
- More precise position sizing (fewer "too small" trades)

🎯 **Next Steps:**
1. Monitor logs for position sizing decisions
2. Track exposure % and WR by confidence tier
3. Adjust multipliers if needed (more aggressive/conservative)
4. Verify evolution selects bots with good high-confidence performance

---

**Questions?** Check logs/arena.log.* for detailed position sizing output.
