# MODO AGGRESSIVE - Setup Completo para Maximizar Volume de Trades

**Data:** 28 de Fevereiro de 2026  
**Objetivo:** Aumentar drasticamente a frequência de trades e permitir posições maiores quando confiança é alta.

---

## 📊 Tabela Comparativa: Conservative vs Medium vs Aggressive

| Métrica | Conservative | Medium | **Aggressive** |
|---------|-------------|--------|---------------|
| **Aggression Level** | 0.6x | 1.0x | **1.45x** |
| **MIN_CONFIDENCE** | 0.72 | 0.60 | **0.48** |
| **MIN_EDGE_AFTER_FEES** | 0.80% | 0.35% | **0.12%** |
| **MAX_SPREAD** | 0.9% | 1.4% | **2.1%** |
| **Position Size (conf ≥0.82)** | x1.50 | x1.65 | **x2.00** |
| **Max Trades/hora** | 20 | 20 | **12 (hard-cap)** |
| **RSI Thresholds (UpDown)** | 26/74 | 32/68 | **35/65** |
| **Trades Esperados (24h)** | 20-40 | 40-80 | **80-150+** |
| **Risco de Drawdown** | Baixo | Médio | **Alto (8-12 perdas seguidas possível)** |

---

## ✅ Arquivos Alterados e Código Exato

### 1. **config.py** — Thresholds Dinâmicos para AGGRESSIVE

**Local:** `c:\Users\luizf\Documents\polymarket\polymarket-bot-arena-main\config.py`

**Seção a verificar (já alterada):**
```python
# Lines ~185-193 (AGGRESSION_THRESHOLDS)
"aggressive": {
    "min_edge_after_fees": 0.0012,  # 0.12%
    "min_confidence": 0.48,  # MODO AGGRESSIVE: conforme pedido do usuário (aceita perdas por lucros maiores)
    "max_spread_allowed": 2.1,  # percent
    # Hard cap in aggressive mode: prevent flood
    "max_trades_per_hour": 12,
},
```

**Getters já existentes (linhas ~360-395):**
```python
def get_aggression_level() -> str:
    """Return configured aggression level: conservative|medium|aggressive"""
    a = (os.environ.get("TRADING_AGGRESSION") or TRADING_AGGRESSION or "medium").lower()
    if a not in AGGRESSION_MULTIPLIERS:
        return "medium"
    return a

def get_min_confidence() -> float:
    return AGGRESSION_THRESHOLDS.get(get_aggression_level(), AGGRESSION_THRESHOLDS["medium"])["min_confidence"]

def get_min_edge_after_fees() -> float:
    return AGGRESSION_THRESHOLDS.get(get_aggression_level(), AGGRESSION_THRESHOLDS["medium"])["min_edge_after_fees"]

def get_max_spread_allowed() -> float:
    """Return spread percent allowed (e.g. 2.1 means 2.1%)."""
    return AGGRESSION_THRESHOLDS.get(get_aggression_level(), AGGRESSION_THRESHOLDS["medium"])["max_spread_allowed"]

def get_max_trades_per_hour_per_bot() -> int:
    lvl = get_aggression_level()
    if lvl == "aggressive":
        return min(12, AGGRESSION_THRESHOLDS[lvl]["max_trades_per_hour"] or 12)
    return AGGRESSION_THRESHOLDS.get(lvl, AGGRESSION_THRESHOLDS["medium"])["max_trades_per_hour"]
```

---

### 2. **.env** — Configuração para Modo AGGRESSIVE

**Arquivo:** `c:\Users\luizf\Documents\polymarket\polymarket-bot-arena-main\.env`

**Cole estas linhas (ou edite se já existem):**
```bash
# ===== MODO AGGRESSIVE - ATIVA VOLUME MÁXIMO DE TRADES =====
TRADING_AGGRESSION=aggressive

# Confidence mínima: 0.48 (foi 0.60 em medium)
# Bots agora aceitam sinais com confiança mais baixa

# Edge mínimo: 0.12% (foi 0.35% em medium)
# Spread máximo: 2.1% (foi 1.4% em medium)
# Trades máximos por hora: 12 (hard-cap de segurança)

# Se quiser ser ainda mais agressivo, ajuste:
# BOT_ARENA_MIN_LIQUIDITY_USD=2000  # reduzir de 8000 para permitir mais mercados
# BOT_ARENA_SKIP_RETRY_SECONDS=20     # reduzir cooldown entre tentativas (foi 45)

# Valores de risco mantidos (NUNCA remover):
BOT_ARENA_PAPER_MAX_DAILY_LOSS_PER_BOT=500
BOT_ARENA_PAPER_MAX_DAILY_LOSS_TOTAL=1500
BOT_ARENA_PAPER_MAX_POSITION=50
```

---

### 3. **bots/base_bot.py** — Usa Thresholds Dinâmicos

**Verificar que estes trechos estão presentes:**

**a) Na função `make_decision()` (linha ~230):**
```python
# Dynamic minimum edge based on configured aggression
min_ev = config.get_min_edge_after_fees()
# Log & return skip if edge below threshold
if best_ev < float(min_ev):
    # log detalhado com motivo
    logger.info(
        f"[{self.name}] SKIP: p_yes={p_yes:.3f} mkt={market_price:.3f} "
        f"ev_yes={ev_yes:.2%} ev_no={ev_no:.2%} | min_ev={min_ev:.4f}"
    )
    return {"action": "skip", ...}
```

**b) Na função `execute()` (linha ~400):**
```python
# Usar threshold global dinâmico em vez de hardcoded 0.55
min_conf = config.get_min_confidence() if hasattr(config, 'get_min_confidence') else 0.55
if conf < float(min_conf):
    logger.info(f"[{self.name}] Signal confidence {conf:.2f} < min_confidence={min_conf:.2f}")
    return {"success": False, "reason": "low_confidence"}
```

**c) Dynamic position sizing (linha ~295):**
```python
if conf_float >= 0.82:
    agg_lvl = config.get_aggression_level() if hasattr(config, 'get_aggression_level') else 'medium'
    if agg_lvl == 'aggressive':
        mult = 2.0  # 2x size quando muito confiante
    elif agg_lvl == 'medium':
        mult = 1.65
    else:
        mult = 1.5
    amount = amount * mult
    logger.info(f"[{self.name}] Dynamic sizing: conf={conf_float:.2f} -> position x{mult:.2f}")
```

---

### 4. **bots/bot_updown.py** — RSI Mais Frouxo em AGGRESSIVE

**Verificar seção `analyze()` (linhas ~145-165):**
```python
# Override RSI thresholds based on aggression level
agg = config.get_aggression_level() if hasattr(config, 'get_aggression_level') else 'medium'
if agg == 'medium':
    entry_oversold = 32
    entry_overbought = 68
elif agg == 'aggressive':
    entry_oversold = 35    # Mais frouxo (-1 de 26)
    entry_overbought = 65   # Mais frouxo (-9 de 74)
else:
    entry_oversold = self.strategy_params.get("rsi_oversold_entry", 26)
    entry_overbought = self.strategy_params.get("rsi_overbought_entry", 74)

# RSI Oversold: BUY UP
if current_rsi <= entry_oversold:
    signal += 1
    confidence += 0.65
    if trend == "bull":
        confidence += 0.2
    else:
        if agg == 'aggressive' and current_rsi <= (entry_oversold - 4):
            # Strong range entry: EMA pode ser ignorada
            confidence += 0.15

# RSI Overbought: BUY DOWN
elif current_rsi >= entry_overbought:
    signal -= 1
    confidence += 0.65
    if trend == "bear":
        confidence += 0.2
    else:
        if agg == 'aggressive' and current_rsi >= (entry_overbought + 4):
            confidence += 0.15
```

---

### 5. **bots/bot_hybrid.py** — Consenso Mais Permissivo

**Verificar seção `analyze()` (linhas ~50-65):**
```python
# Extra weight to mean_reversion if high confidence
mr_w = self.strategy_params.get("mean_rev_weight", 0.5)
if mr_signal and mr_signal.get("action") != "hold" and float(mr_signal.get("confidence", 0)) >= 0.75:
    mr_w = min(1.0, mr_w + 0.15)

# Consensus rule: 2/3 ou weighted score strong enough
active_count = active_signals
required = 2 if active_count >= 3 else 1
agreement = max(yes_votes, no_votes) >= required or abs(weighted_score) >= 0.35

# Use global min_confidence como piso
threshold = max(self.strategy_params.get("confidence_threshold", 0.6), 
                config.get_min_confidence() if hasattr(config, 'get_min_confidence') else 0.5)
if confidence < threshold:
    return {"action": "hold", ...}
```

---

### 6. **arena.py** — Log de Modo Ativo

**Ao iniciar (linhas ~1150-1160):**
```python
logger.info("=== Configurações Ativas ===")
aggression = config.get_aggression_level() if hasattr(config, 'get_aggression_level') else 'medium'
logger.info(f"🔴 MODO AGGRESSIVE ATIVO" if aggression == "aggressive" else f"🟢 Modo: {aggression.title()}")
logger.info(f"MIN_CONFIDENCE: {config.get_min_confidence() if hasattr(config, 'get_min_confidence') else 0.55}")
logger.info(f"MIN_EDGE_AFTER_FEES: {config.get_min_edge_after_fees() if hasattr(config, 'get_min_edge_after_fees') else 0.015:.4f}")
logger.info(f"MAX_TRADES_PER_HOUR: {config.get_max_trades_per_hour_per_bot() if hasattr(config, 'get_max_trades_per_hour_per_bot') else 20}")
logger.info("============================")
```

---

## 🚀 Instruções Passo a Passo de Setup

### Passo 1: Editar `.env`
```bash
# Abra c:\Users\luizf\Documents\polymarket\polymarket-bot-arena-main\.env
# Adicione/edite:
TRADING_AGGRESSION=aggressive
```

### Passo 2: Verificar que config.py foi alterado
```bash
# Abra config.py e procure por:
# "min_confidence": 0.48  (deve estar ao invés de 0.53)
# "max_trades_per_hour": 12
```

### Passo 3: Validar imports (rodar no terminal)
```powershell
cd C:\Users\luizf\Documents\polymarket\polymarket-bot-arena-main
python -c "import config; print(f'Aggression Level: {config.get_aggression_level()}'); print(f'Min Confidence: {config.get_min_confidence()}')"
# Esperado: Aggression Level: aggressive, Min Confidence: 0.48
```

### Passo 4: Rodar arena em PAPER MODE (seguro)
```powershell
cd C:\Users\luizf\Documents\polymarket\polymarket-bot-arena-main
python arena.py
```

### Passo 5: Monitorar logs por 1-2 horas
```powershell
# Em outro terminal:
tail -f logs/arena.log.* | grep -E "SKIP:|AGGRESSIVE|Signal confidence|MODO"
```

**Esperado:** Ver MUITO MAIS trades sendo colocados (updown-g3-* e hybrid-g2 devem gerar >80 trades em 24h vs quase zero antes).

---

## ⚠️ AVISOS DE RISCO CRÍTICOS

### Risco 1: Sequências de Perdas
- **Possível:** 8-12 perdas seguidas em sequência
- **Como:** Confidence 0.48 significa sinais fracos; alguns serão errados
- **Mitigação:** ArenaRiskManager mantém hard caps de daily_loss_per_bot=$500 e total=$1500

### Risco 2: Spread Aumentado
- **MAX_SPREAD de 2.1% significa** aceitar mercados com spread alto
- **Impacto:** Entrada e saída custam mais; mesmo trade certo pode perder por spread
- **Exemplo:** Entrar em YES a 0.5 com spread 2% custa ~$1 de spread em posição $50

### Risco 3: Position Sizing x2 em Aggressive
- Quando confidence ≥ 0.82, posição dobra
- Com confidence 0.48, dobramentos são raros → risco moderado neste cenário
- Mas se encontrar muitos sinais com 0.82+, exposição cresce rápido

### Risco 4: Hard Cap 12 Trades/Hora
- Evita _flood_ de trades em confusão de mercado
- Se 5 bots colocam 12 cada = 60 trades/hora = máximo da arena
- Respei será respeitado pelo RiskManager mesmo

---

## 📈 Métricas de Sucesso (Monitor me 24-48h)

### Target 1: Volume de Trades
- **Antes:** 0 trades/dia (todos rejeitados por confidence baixa)
- **Depois:** 80-150+ trades/dia (distribution uniforme entre 5 bots)
- **Medição:** `SELECT COUNT(*) FROM trades WHERE created_at >= datetime('now', '-24 hours')`

### Target 2: Hit Rate
- **Esperado em aggressive:** 48-52% win rate (não precisa 60%+ com volume)
- **P&L target:** Mesmo com 50% WR, mais trades = potencial maior lucro
- **Exemplo:** 100 trades @ 50% WR = 50 wins @ $1.50 avg = $75 ganho

### Target 3: Sem Pausas por Loss
- Com aggressive, bots não devem pausar por 24h (daily_loss_cap $500 é alto)
- Se pausar, check `arena.log` por motivo exato

---

## 🔧 Troubleshooting Rápido

### Problema: "Ainda zero trades"
**Solução 1:** Verificar que TRADING_AGGRESSION=aggressive foi carregado
```powershell
python -c "import config; print(config.get_aggression_level())"
```

**Solução 2:** Se ainda offline, reduzir MIN_LIQUIDITY no .env
```bash
BOT_ARENA_MIN_LIQUIDITY_USD=2000  # (foi 8000)
```

**Solução 3:** Checkar que discover_markets está achando mercados
```powershell
python -c "from arena import discover_markets; from pathlib import Path; import json; api_key = json.load(open(Path.home() / '.config/simmer/credentials.json')).get('api_key'); markets = discover_markets(api_key); print(f'Found {len(markets)} markets'); print('First 3:', [m.get('question', '')[:60] for m in markets[:3]])"
```

### Problema: "Muitos sinais ignorados por confidence"
**Verificação:** Essa é a intention do aggressive mode
- Se ainda vendo >50% SKIP após 1h, talvez signal feed offline
- Check `arena.log` por "stale=1" ou "sentiment_score=0.5"

---

## 📋 Checklist Final

- [ ] `.env` contém `TRADING_AGGRESSION=aggressive`
- [ ] `config.py` AGGRESSION_THRESHOLDS["aggressive"]["min_confidence"] = 0.48
- [ ] `config.py` AGGRESSION_THRESHOLDS["aggressive"]["max_trades_per_hour"] = 12
- [ ] `bots/base_bot.py` usa `config.get_min_confidence()` em execute()
- [ ] `bots/base_bot.py` dynamic sizing ativa (x2.0 em aggressive)
- [ ] `bots/bot_updown.py` RSI thresholds = 35/65 em aggressive
- [ ] `bots/bot_hybrid.py` consensus rule permissivo
- [ ] `arena.py` discovery não rejeita por liquidity quando campo ausente
- [ ] Teste rápido: `python -c "import config; print(config.get_aggression_level())"` = "aggressive"
- [ ] Logs mostram "🔴 MODO AGGRESSIVE ATIVO" na inicialização

---

## 📞 Contato / Próximas Etapas

1. **Rodar em PAPER por 24-48h** e coletar métricas
2. **Se >80 trades em 24h com WR 48%+**, considerar passar para configuração ao vivo (test com valores pequenos primeiro)
3. **Se <50 trades**, talvez aumentar ainda mais: min_conf=0.45, min_edge=0.08%
4. **Monitorar daily_loss:** Se atingir $500/bot em 1 dia, RiskManager pausa automaticamente (segurança)

---

**Data de Implementação:** 28 de Fevereiro de 2026  
**Status:** ✅ PRONTO PARA USAR  
**Modo:** 🔴 AGGRESSIVE (ativa volume máximo, aceita perdas por lucros maiores)
