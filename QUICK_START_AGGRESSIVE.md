# 🚀 QUICK START - MODO AGGRESSIVE (5 minutos)

**Objetivo:** Colocar bots em modo AGGRESSIVE para maximizar volume de trades (80-150+/dia).  
**Risco:** Aceita perdas, mas lucros são maiores quando acerta.

---

## ⚡ Passo 1: Editar `.env` (1 minuto)

Abra: `c:\Users\luizf\Documents\polymarket\polymarket-bot-arena-main\.env`

**Procure por:**
```
TRADING_AGGRESSION=medium
```

**Substitua por:**
```
TRADING_AGGRESSION=aggressive
```

**Se não existir, adicione no final do arquivo:**
```bash
# ===== MODO AGGRESSIVE =====
TRADING_AGGRESSION=aggressive
```

---

## ✅ Passo 2: Validar Configuração (1 minuto)

Abra terminal PowerShell:
```powershell
cd C:\Users\luizf\Documents\polymarket\polymarket-bot-arena-main
python -c "import config; print(f'✓ Aggression: {config.get_aggression_level()}'); print(f'✓ Min Confidence: {config.get_min_confidence()}'); print(f'✓ Min Edge: {config.get_min_edge_after_fees():.4f}'); print(f'✓ Max Trades/hr: {config.get_max_trades_per_hour_per_bot()}')"
```

**Esperado:**
```
✓ Aggression: aggressive
✓ Min Confidence: 0.48
✓ Min Edge: 0.0012
✓ Max Trades/hr: 12
```

---

## 🎯 Passo 3: Rodar Arena em PAPER (1 minuto)

```powershell
cd C:\Users\luizf\Documents\polymarket\polymarket-bot-arena-main
python arena.py
```

**Veja nos logs:**
```
🔴 MODO AGGRESSIVE ATIVO - MIN_CONFIDENCE=0.48, MIN_EDGE=0.12%, TRADES MÁXIMOS=150+/dia
```

---

## 📊 Passo 4: Monitorar Trades (2 minutos)

**Em outro terminal, rode:**
```powershell
# Ver últimas linhas de log real-time
Get-Content logs/arena.log.* -Wait | Select-String -Pattern "SKIP|AGGRESSIVE|Signal|trade"
```

**Ou em Python:**
```powershell
python -c "import db; trades = db.get_trader_history('updown-g3-184', limit=10); print(f'Últimos trades: {len(trades)}'); [print(f'{t[\"id\"]}: {t[\"side\"]} @ {t[\"created_at\"]}') for t in trades]"
```

---

## ⚠️ Riscos Críticos

| Risco | Mitigation |
|-------|-----------|
| **Sequência 8-12 perdas** | RiskManager para bot ao atingir daily_loss=$500 |
| **Spread alto (2.1%)** | Position sizing pequeno ($50) mitiga dano |
| **Volume 150 trades/dia** | Hard cap 12 trades/hora por bot (máx 60 globais) |
| **Confidence 0.48 = sinal fraco** | Esperado; WR será ~50%, não 70% |

---

## 🎓 O Que Muda?

| Métrica | Antes (medium) | Depois (aggressive) |
|--------|-------------|-----------------|
| MIN_CONFIDENCE | 0.60 | **0.48** ⬇️ |
| MIN_EDGE | 0.35% | **0.12%** ⬇️ |
| RSI Thresholds | 32/68 | **35/65** ⬇️ |
| Position Size (conf≥0.82) | 1.65x | **2.0x** ⬆️ |
| Max Spread | 1.4% | **2.1%** ⬆️ |
| **Trades Esperados** | 40-80 | **80-150+** ⬆️ |

---

## 📈 Métricas de Sucesso (24h)

**Target 1:** Mínimo 80 trades em 24h  
`SELECT COUNT(*) FROM trades WHERE created_at >= datetime('now', '-24 hours')`

**Target 2:** WR 48-52% (ok com confidence 0.48)  
`SELECT ROUND(SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END)*100.0/COUNT(*), 1) as win_rate FROM trades WHERE outcome IN ('win','loss')`

**Target 3:** Sem pausas por daily_loss (cap=$500 é alto)  
`SELECT * FROM trades WHERE reason LIKE '%daily_loss%'`

---

## 🔧 Se Não Funcionar

**Problema: "Ainda vendo min_conf=0.60"**
- Verificar que `.env` foi salvo (não editou arquivo errado)
- Reiniciar Python interpreter: `exit()` e rodar `python arena.py` de novo
- Check: `python -c "import os; print(os.getenv('TRADING_AGGRESSION'))"`

**Problema: "Zero trades ainda after 1h"**
- Checar markets discovery: `python -c "from arena import discover_markets; ..."`
- Reduzir NO.env: `BOT_ARENA_MIN_LIQUIDITY_USD=2000` (era 8000)
- Checar logs por "Spread" ou "Liquidity" rejections

**Problema: "Position sizing é mínima (não x2)"**
- Isso é normal em aggressive (confidence 0.48 raramente chega 0.82)
- Posição aumenta quando signal base é muito strong (confidence≥0.82)

---

## ✨ Checklist Antes de Rodar

- [ ] `.env` tem `TRADING_AGGRESSION=aggressive`
- [ ] `python -c "import config; print(config.get_aggression_level())"` = "aggressive"
- [ ] Paper mode ativo (`TRADING_MODE = "paper"` em config.py)
- [ ] RiskManager ativo (daily_loss_cap=$500)
- [ ] Logs mostram "🔴 MODO AGGRESSIVE ATIVO" na inicialização
- [ ] Mínimo 1-2h de execução antes de avaliar

---

## 🎯 Próximas Etapas (depois de 24-48h)

1. Coletar métricas: total trades, WR, P&L
2. Se >100 trades + WR 48%+:
   - Aumentar position size: `BOT_ARENA_PAPER_MAX_POSITION=100`
   - Ou passar para live com $10 limit
3. Se <50 trades:
   - Aumentar agressividade: `min_conf=0.45` ou `min_edge=0.08%` em config.py

---

**Tempo Total:** ~5 minutos  
**Status:** ✅ Pronto para Colocar Trades  
**Modo:** 🔴 AGGRESSIVE (conforme pedido - aceita perdas por lucros maiores)
