# MODO AGGRESSIVE — FINAL CHECKLIST ✓

Complete this checklist before running arena.py in AGGRESSIVE mode.

---

## 📋 STEP 1: Activate Configuration

### Option A: PowerShell (Fastest)
```powershell
.\activate_aggressive.ps1
```

### Option B: Manual
1. Edit `.env` file (or create if missing):
   ```
   TRADING_AGGRESSION=aggressive
   BOT_ARENA_MODE=paper
   ```
2. Save and close

---

## 🔍 STEP 2: Validate Configuration

Run this Python snippet to verify settings loaded correctly:

```python
python -c "
import config
print('Aggression Level:', config.get_aggression_level())
print('Min Confidence:', config.get_min_confidence())
print('Min Edge:', f'{config.get_min_edge_after_fees():.6f}')
print('Max Spread:', f'{config.get_max_spread_allowed()}%')
print('Max Trades/Hour:', config.get_max_trades_per_hour_per_bot())
print('Mode:', config.get_current_mode())
"
```

**Expected Output:**
```
Aggression Level: aggressive
Min Confidence: 0.48
Min Edge: 0.001200
Max Spread: 2.1%
Max Trades/Hour: 12
Mode: paper
```

If values don't match, edit `.env` and try again.

---

## 🚀 STEP 3: Start Arena

### Full Status + Arena (Recommended)
```bash
python tools/aggressive_status.py
python arena.py
```

### Arena Only
```bash
python arena.py
```

### Expected Startup Log
Look for **green** output showing:
```
🔴 MODO AGGRESSIVE ATIVO
├─ MIN_CONFIDENCE: 0.48
├─ MIN_EDGE_AFTER_FEES: 0.0012 (0.12%)
├─ MAX_SPREAD_ALLOWED: 2.1%
├─ MAX_TRADES_PER_HOUR: 12
└─ POSITION_SIZE_MULTIPLIER: 2.0x (when confidence ≥ 0.82)
```

---

## 📊 STEP 4: Monitor Real-Time Performance (CRITICAL)

### Check Trade Volume (Every 30 min)
```python
python -c "
import db
from datetime import datetime, timedelta
with db.get_conn() as conn:
    t24 = (datetime.now() - timedelta(hours=24)).isoformat()
    result = conn.execute(
        'SELECT COUNT(*) as count FROM trades WHERE created_at > ?', (t24,)
    ).fetchone()
    print(f'Trades in 24h: {result[\"count\"]}')
"
```

**Targets:**
- ✅ 80-150+ trades in 24h (GOAL)
- ⚠️  20-80 trades = ramping up (OK)
- ❌ <20 trades = config not applied or API down

### Check Win Rate (Every 2 hours)
```python
python -c "
import db
with db.get_conn() as conn:
    result = conn.execute('''
        SELECT bot_name,
               COUNT(*) as total,
               SUM(CASE WHEN outcome=\"win\" THEN 1 ELSE 0 END) as wins,
               ROUND(100.0 * SUM(CASE WHEN outcome=\"win\" THEN 1 ELSE 0 END) / COUNT(*), 1) as wr
        FROM trades
        WHERE outcome IS NOT NULL
        GROUP BY bot_name
        ORDER BY wr DESC
    ''').fetchall()
    for r in result:
        print(f'{r[\"bot_name\"]}: {r[\"total\"]} trades, {r[\"wr\"]}% WR ({r[\"wins\"]} wins)')
"
```

**Expected Ranges:**
- 45-55% WR = Normal (signal quality degraded by min_conf=0.48, but edge still positive)
- 35-45% WR = Acceptable (still paper trading)
- <35% WR = Investigate (possible issue with signal calculation)
- >55% WR = Excellent (unlikely but possible)

### Check Daily P&L (Multiple Times Daily)
```python
python -c "
import db
from datetime import datetime
today = datetime.now().strftime('%Y-%m-%d')
with db.get_conn() as conn:
    result = conn.execute(f'''
        SELECT bot_name,
               ROUND(SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END), 2) as profit,
               ROUND(SUM(CASE WHEN pnl < 0 THEN -pnl ELSE 0 END), 2) as loss,
               ROUND(SUM(pnl), 2) as net_pnl
        FROM trades
        WHERE date(created_at) = '{today}'
        GROUP BY bot_name
    ''').fetchall()
    for r in result:
        print(f'{r[\"bot_name\"]}: +${r[\"profit\"]:.2f} (profit) -${r[\"loss\"]:.2f} (loss) = ${r[\"net_pnl\"]:.2f}')
"
```

**Critical Thresholds (Risk Manager):**
- ⚠️  **Daily loss per bot hits $500** = Bot PAUSED (automatic) for rest of day
- ⚠️  **Total loss across all 4 bots hits $1500** = ALL BOTS PAUSED for rest of day
- ✅ Daily loss <$250/bot = Healthy

---

## ⚠️ STEP 5: Awareness - What Will Change

### Trade Frequency
| Metric | Before | After (AGGRESSIVE) |
|--------|--------|-------------------|
| Trades/day | 0-5 | 80-150+ |
| Avg confidence | Rejected @ <0.60 | 0.48-0.75 |
| Acceptance rate | ~10% | ~70-80% |

### Risk Profile
| Metric | After (AGGRESSIVE) |
|--------|-------------------|
| Max daily loss/bot | $500 (Hard limit) |
| Acceptable WR | 45-55% (lower due to min_conf=0.48) |
| Typical spread cost | ~$0.50-$1.50 per $50 trade |
| Consecutive losses before pause | 3+ → total $500 loss |

### Profitability Timeline
- **Days 1-2:** Accumulating trades, WR stabilizing (might miss edge)
- **Days 3-5:** 50-100 resolved trades, true WR visible, P&L direction clear
- **Week 2+:** 200+ trades, reliable edge measurement, evolution kicking in

---

## 🛠️ STEP 6: Troubleshooting

### Problem: Still seeing 0 trades after 1 hour

**Cause 1: .env not loaded**
- Check: Is `TRADING_AGGRESSION=aggressive` in .env?
- Fix: Edit .env, restart Python (not just arena, full restart)

**Cause 2: Market discovery broken**
- Check: `python -c "from arena import discover_markets; m = discover_markets(); print(f'Found {len(m)} markets')"`
- Expected: >0 markets found
- If 0: Simmer API might be down or rate-limited; wait 5min and retry

**Cause 3: Config getter missing**
- Check: `python -c "import config; config.get_aggression_level()"`
- If error: Syntax error in config.py (check indentation)
- Fix: Re-apply patches from AGGRESSIVE_MODE_SETUP.md

### Problem: Trades are trading but WR is <35%

**Causes:**
1. Signal quality degraded (expected at min_conf=0.48)
2. Spread eating into profits (2.1% max spread is aggressive)
3. Market volatility surge (BTC price swinging wildly)

**Fix:**
- Wait 24h for more data before evaluating
- Check learning.py to see if bot_learning table has data (features recorded)
- If WR stays <35% after 48h, revert to medium mode: edit `.env` → `TRADING_AGGRESSION=medium`

### Problem: Daily loss exceeded $500, bots stopped trading

**Why:** RiskManager safety feature (automatic)

**Recover:**
1. Wait until next calendar day (midnight UTC) — RiskManager resets daily
2. OR manually edit bot config in database (not recommended without understanding trade history)

**Prevention:**
- Monitor P&L every 2 hours
- Reduce min_conf to 0.50 if losing streak detected
- Check if market volatility spiked

### Problem: Arena crashed

**Check logs:**
```bash
tail -50 logs/arena.log.*
```

**Common causes:**
- Database locked (sqlite3 contention)
- API rate limit hit
- Memory exhaustion (unlikely)
- IndentationError in bot code (should be fixed, but verify)

**Recovery:**
1. Wait 30 seconds
2. Re-run: `python arena.py`
3. If crashes immediately, check logs for Python error

---

## 📈 STEP 7: Long-Term Monitoring (After 48 hours)

### Evaluation Criteria

**✅ Success Indicators:**
- Trades/day: 80-150+ (target met)
- WR: 45-55% (acceptable for min_conf=0.48)
- Daily net P&L: -$50 to +$50 (breakeven-ish, which is OK given spread costs)
- Evolution: Bots evolving every 4 hours, top 2 survivors replicating

**⚠️ Caution Indicators (Investigate but don't panic):**
- WR: 35-45% (lower than ideal but still viable)
- Daily P&L: -$100 to -$200 (losing streak, normal variance)
- One bot paused daily (hit loss limit)

**❌ Failure Indicators (Consider reverting):**
- Trades/day: <20 (config not applied)
- WR: <30% (signal quality broken)
- Daily loss: -$500+ consistently (sustain losses)
- Multiple bots paused on same day (cascading failures)

### If Failing: Revert to Medium Mode
```bash
# Edit .env:
TRADING_AGGRESSION=medium

# Save and restart arena.py
python arena.py
```

---

## 📞 Support Reference

**Quick Commands:**
```bash
# Check current status
python tools/aggressive_status.py

# View last 50 trades
sqlite3 data/arena.db "SELECT bot_name, side, outcome, pnl FROM trades ORDER BY id DESC LIMIT 50;"

# View evolution events
sqlite3 data/arena.db "SELECT * FROM evolution_events ORDER BY timestamp DESC LIMIT 10;"

# Clear test trades (use with caution!)
# sqlite3 data/arena.db "DELETE FROM trades WHERE MODE='paper' AND strftime('%Y-%m-%d', created_at) = date('today');"
```

**Documentation:**
- Full setup: See `AGGRESSIVE_MODE_SETUP.md`
- Quick ref: See `QUICK_START_AGGRESSIVE.md`
- Status check: Run `python tools/aggressive_status.py`

---

## ✅ FINAL CHECKLIST

- [ ] `.env` file created/updated with `TRADING_AGGRESSION=aggressive`
- [ ] Python config validation passed (expected values match)
- [ ] Arena started and showing "🔴 MODO AGGRESSIVE ATIVO" on boot
- [ ] First 10 trades placed within first 30 minutes
- [ ] Trade volume >80 in 24 hours (or 5 trades/hour average)
- [ ] Win rate stabilizing between 45-55%
- [ ] Daily loss limit working (no trades after hitting $500)
- [ ] Monitoring schedule set (check status every 2 hours first day)
- [ ] Backup plan ready (revert to medium if needed)

---

**🎯 TARGET OUTCOME:**
After 48 hours, 150+ trades with 48-52% WR, daily P&L near breakeven (acceptable given spread costs), bots evolving healthily, and arena running stably with minimal manual intervention.

**Good luck! 🚀**
