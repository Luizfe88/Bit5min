"""FastAPI dashboard backend for the Bot Arena."""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import config
import db
import learning
from core.risk_manager import risk_manager

# Configurar logger
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uvicorn")
logger.info(f"🚀 Iniciando Dashboard Server")
logger.info(f"📂 DB_PATH: {db.DB_PATH}")

app = FastAPI(title="Polymarket Bot Arena Dashboard")

_WALLET_CACHE = {"ts": 0.0, "value": None}
_WALLET_CACHE_TTL_SEC = 10
_MARKETS_CACHE = {"ts": 0.0, "value": None}
_MARKETS_CACHE_TTL_SEC = 10


def _env_float(name: str):
    v = os.environ.get(name)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _load_simmer_api_keys():
    keys = []
    try:
        data = json.load(open(config.SIMMER_API_KEY_PATH))
        k = data.get("api_key")
        if k:
            keys.append(k)
    except Exception:
        pass

    try:
        if config.SIMMER_BOT_KEYS_PATH.exists():
            bot_map = json.load(open(config.SIMMER_BOT_KEYS_PATH))
            if isinstance(bot_map, dict):
                for v in bot_map.values():
                    if v:
                        keys.append(v)
    except Exception:
        pass

    uniq = []
    seen = set()
    for k in keys:
        if k in seen:
            continue
        seen.add(k)
        uniq.append(k)
    return uniq


def _fetch_simmer_balance(api_key: str):
    import requests as req
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = req.get(f"{config.SIMMER_BASE_URL}/api/sdk/agents/me", headers=headers, timeout=10)
    if resp.status_code != 200:
        raise RuntimeError(f"Simmer agents/me failed: {resp.status_code} {resp.text[:200]}")
    data = resp.json()
    bal = data.get("balance")
    try:
        bal = float(bal)
    except (TypeError, ValueError):
        bal = None
    return {"balance": bal, "raw": data}


def _parse_iso_utc(ts: str):
    if not ts:
        return None
    if not isinstance(ts, str):
        return None
    try:
        s = ts.strip().replace("Z", "+00:00")
        if " " in s and "T" not in s:
            s = s.replace(" ", "T", 1)
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _fmt_amount_usd(val):
    try:
        x = float(val)
    except (TypeError, ValueError):
        return None
    if abs(x) < 0.01:
        return f"{x:.4f}"
    return f"{x:.2f}"


def _fmt_pnl_usd(val):
    try:
        x = float(val)
    except (TypeError, ValueError):
        return None
    if abs(x) < 0.01:
        s = f"{abs(x):.4f}"
    else:
        s = f"{abs(x):.2f}"
    return f"+${s}" if x >= 0 else f"-${s}"


def _fetch_simmer_markets(api_key: str, status: str = "active", limit: int = 200):
    import requests as req

    headers = {"Authorization": f"Bearer {api_key}"}
    resp = req.get(
        f"{config.SIMMER_BASE_URL}/api/sdk/markets",
        headers=headers,
        params={"status": status, "limit": int(limit)},
        timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Simmer markets failed: {resp.status_code} {resp.text[:200]}")
    data = resp.json()
    return data if isinstance(data, list) else data.get("markets", [])


def _get_market_close_map():
    now = time.time()
    cached = _MARKETS_CACHE.get("value")
    if cached and (now - float(_MARKETS_CACHE.get("ts", 0))) < _MARKETS_CACHE_TTL_SEC:
        return cached

    try:
        api_key = json.load(open(config.SIMMER_API_KEY_PATH))["api_key"]
    except Exception:
        _MARKETS_CACHE["ts"] = now
        _MARKETS_CACHE["value"] = {}
        return {}

    try:
        markets_list = _fetch_simmer_markets(api_key, status="active", limit=200)
    except Exception:
        _MARKETS_CACHE["ts"] = now
        _MARKETS_CACHE["value"] = {}
        return {}

    m = {}
    for mk in markets_list:
        mid = mk.get("id")
        if not mid:
            continue
        m[mid] = {
            "resolves_at": mk.get("resolves_at"),
            "question": mk.get("question"),
            "url": mk.get("url"),
        }

    _MARKETS_CACHE["ts"] = now
    _MARKETS_CACHE["value"] = m
    return m


def _get_open_exposure(mode: str):
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(amount), 0) s FROM trades WHERE outcome IS NULL AND mode=?",
            (mode,),
        ).fetchone()
        c = int(dict(row)["c"])
        s = float(dict(row)["s"])
        by_bot = conn.execute(
            "SELECT bot_name, COUNT(*) c, COALESCE(SUM(amount), 0) s FROM trades WHERE outcome IS NULL AND mode=? GROUP BY bot_name",
            (mode,),
        ).fetchall()
        by_bot = [{"bot_name": r["bot_name"], "open_trades": int(r["c"]), "invested": float(r["s"])} for r in by_bot]
    return {"open_trades": c, "invested": s, "by_bot": by_bot}


def _get_realized_pnl(mode: str):
    with db.get_conn() as conn:
        row_all = conn.execute(
            """SELECT COALESCE(SUM(pnl), 0) s
               FROM trades
               WHERE mode=? AND pnl IS NOT NULL
                 AND NOT (outcome IS NOT NULL AND pnl = 0)""",
            (mode,),
        ).fetchone()
        row_today = conn.execute(
            """SELECT COALESCE(SUM(pnl), 0) s
               FROM trades
               WHERE mode=? AND pnl IS NOT NULL
                 AND NOT (outcome IS NOT NULL AND pnl = 0)
                 AND date(created_at) = date('now')""",
            (mode,),
        ).fetchone()
    return {"all_time": float(dict(row_all)["s"]), "today": float(dict(row_today)["s"])}


@app.get("/api/wallet")
async def get_wallet():
    now = time.time()
    cached = _WALLET_CACHE.get("value")
    if cached and (now - float(_WALLET_CACHE.get("ts", 0))) < _WALLET_CACHE_TTL_SEC:
        return cached

    mode = config.get_current_mode()
    exposure = _get_open_exposure(mode)
    realized = _get_realized_pnl(mode)
    virtual_bankroll = None # Declarar antes
    
    # FIX: Tentar obter do DB (estado do arena.py) ou usar config.PAPER_STARTING_BALANCE
    db_bankroll = db.get_arena_state("virtual_bankroll")
    if db_bankroll:
        virtual_bankroll = float(db_bankroll)
    else:
        # Fallback para o valor configurado
        val = _env_float("BOT_ARENA_DASHBOARD_VIRTUAL_BANKROLL")
        virtual_bankroll = val if val is not None else config.PAPER_STARTING_BALANCE

    virtual_equity = None
    virtual_available = None
    if virtual_bankroll is not None:
        virtual_equity = float(virtual_bankroll) + float(realized["all_time"])
        virtual_available = float(virtual_equity) - float(exposure["invested"])
        
        # Não atualizar o RiskManager aqui, deixe o arena.py gerenciar isso
        # Apenas lemos o estado compartilhado via DB


    payload = {
        "mode": mode,
        "venue": config.get_venue(),
        "cash_balance_total": None,
        "virtual_bankroll": virtual_bankroll,
        "virtual_equity": virtual_equity,
        "virtual_available": virtual_available,
        "virtual_cash": virtual_available,
        "realized_pnl_all_time": realized["all_time"],
        "realized_pnl_today": realized["today"],
        "accounts": None,
        "open_trades": exposure["open_trades"],
        "invested_open": exposure["invested"],
        "by_bot": exposure["by_bot"],
        "error": None,
    }

    if mode == "paper":
        keys = _load_simmer_api_keys()
        if not keys:
            payload["error"] = "Nenhuma API key do Simmer encontrada (credentials.json / bot_keys.json)"
        else:
            accounts = []
            total = 0.0
            for i, k in enumerate(keys):
                info = _fetch_simmer_balance(k)
                bal = info["balance"]
                accounts.append({"idx": i, "balance": bal})
                if bal is not None:
                    total += bal
            payload["cash_balance_total"] = total
            
            # Se não temos virtual_bankroll no DB, usamos o config padrão
            if not db_bankroll:
                # Revertido: Não usa saldo real da API, mantém o saldo virtual configurado
                # para isolamento do ambiente de teste
                pass
                
                # Código antigo removido:
                # if total > 0 and abs(total - config.PAPER_STARTING_BALANCE) > 1:
                #     ...
    else:
        payload["error"] = "Saldo live não suportado no dashboard (apenas paper/Simmer)."

    _WALLET_CACHE["ts"] = now
    _WALLET_CACHE["value"] = payload
    return payload


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "index.html"
    return html_path.read_text(encoding="utf-8")


@app.get("/api/status")
async def get_status():
    return {
        "mode": config.get_current_mode(),
        "venue": config.get_venue(),
        "max_position": config.get_max_position(),
        "max_daily_loss_per_bot": config.get_max_daily_loss_per_bot(),
        "max_daily_loss_total": config.get_max_daily_loss_total(),
    }


@app.post("/api/mode")
async def set_mode(request: Request):
    body = await request.json()
    mode = body.get("mode")
    if mode not in ("paper", "live"):
        return JSONResponse({"error": "Mode must be 'paper' or 'live'"}, 400)
    config.set_trading_mode(mode)
    return {"mode": config.get_current_mode()}


@app.post("/api/reset-day")
async def reset_day(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    mode = body.get("mode") or config.get_current_mode()
    print(f"[RESET-DAY] Recebido modo: {mode}")
    if mode not in ("paper", "live"):
        print(f"[RESET-DAY] Modo inválido: {mode}")
        return JSONResponse({"error": "invalid mode"}, 400)
    try:
        db.reset_arena_day(mode)
        print(f"[RESET-DAY] Reset realizado com sucesso para modo: {mode}")
        return {"ok": True, "mode": mode}
    except Exception as e:
        print(f"[RESET-DAY] Erro ao resetar: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": "internal error"}, 500)


# REMOVED DUPLICATE /api/positions endpoint


@app.get("/api/markets")
async def get_markets():
    """Get active BTC 5-min markets with close times."""
    import requests as req
    try:
        now_dt = datetime.now(timezone.utc)
        max_dt = now_dt + timedelta(hours=1)
        api_key = json.load(open(config.SIMMER_API_KEY_PATH))["api_key"]
        headers = {"Authorization": f"Bearer {api_key}"}
        resp = req.get(
            f"{config.SIMMER_BASE_URL}/api/sdk/markets",
            headers=headers,
            params={"status": "active", "limit": 50},
            timeout=10,
        )
        data = resp.json()
        markets_list = data if isinstance(data, list) else data.get("markets", [])
        btc_markets = []
        for m in markets_list:
            q = m.get("question", "").lower()
            if "bitcoin" in q and "up or down" in q:
                resolves_at = m.get("resolves_at")
                dt = _parse_iso_utc(resolves_at)
                if not dt:
                    continue
                if not (now_dt <= dt <= max_dt):
                    continue
                btc_markets.append({
                    "id": m.get("id"),
                    "question": m.get("question"),
                    "current_price": m.get("current_price"),
                    "resolves_at": resolves_at,
                    "url": m.get("url"),
                })
        return JSONResponse(btc_markets)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/overview")
async def get_overview():
    stats = db.get_dashboard_stats()
    active_bots = db.get_active_bots()
    return JSONResponse({
        "stats": stats,
        "active_bots": active_bots,
        "mode": config.get_current_mode(),
    })


@app.get("/api/bots")
async def get_bots():
    # Obtém apenas bots ativos do DB
    active = db.get_active_bots()
    
    # Aplica a mesma lógica de ordenação e limite do arena.py para garantir consistência
    try:
        max_bots = getattr(config, "NUM_BOTS", 5)
        # Prioriza configs mais recentes por geração e created_at
        active = sorted(
            active,
            key=lambda r: (int(r.get("generation", 0) or 0), str(r.get("created_at", ""))),
            reverse=True
        )[:max_bots]
    except Exception:
        active = active[:getattr(config, "NUM_BOTS", 5)]
    
    result = []
    for bot_cfg in active:
        # Parse params JSON string if needed
        cfg = dict(bot_cfg)
        if isinstance(cfg.get("params"), str):
            try:
                cfg["params"] = json.loads(cfg["params"])
            except (json.JSONDecodeError, TypeError):
                pass
        perf_6h = db.get_bot_performance(cfg["bot_name"], hours=6)
        perf_24h = db.get_bot_performance(cfg["bot_name"], hours=24)
        trades = db.get_bot_trades(cfg["bot_name"], limit=10)
        # Count pending (unresolved) trades so dashboard shows activity
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as c FROM trades WHERE bot_name=? AND outcome IS NULL AND mode=?",
                (cfg["bot_name"], config.get_current_mode())
            ).fetchone()
            pending_count = dict(row)["c"]
        result.append({
            "config": cfg,
            "performance_6h": perf_6h,
            "performance_24h": perf_24h,
            "win_rate_6h": perf_6h.get('win_rate', 0),
            "recent_trades": trades,
            "pending_trades": pending_count,
        })
    return JSONResponse(result)


@app.get("/api/evolution")
async def get_evolution():
    history = db.get_evolution_history(limit=20)
    for h in history:
        for key in ("survivors", "replaced", "new_bots", "rankings"):
            if isinstance(h.get(key), str):
                h[key] = json.loads(h[key])
    return JSONResponse(history)


# REMOVED DUPLICATE /api/positions endpoint



@app.get("/api/trades")
async def get_trades(bot: str = None, limit: int = 50):
    close_map = _get_market_close_map()
    if bot:
        trades = db.get_bot_trades(bot, limit=limit)
        for t in trades:
            t["amount_display"] = _fmt_amount_usd(t.get("amount"))
            t["pnl_display"] = _fmt_pnl_usd(t.get("pnl")) if t.get("pnl") is not None else None
            mid = t.get("market_id")
            info = close_map.get(mid) if mid else None
            resolves_at = info.get("resolves_at") if info else None
            t["market_resolves_at"] = resolves_at
            if t.get("outcome") is None:
                t["close_at"] = resolves_at
                dt = _parse_iso_utc(resolves_at)
                t["close_in_seconds"] = int((dt - datetime.now(timezone.utc)).total_seconds()) if dt else None
            else:
                t["close_at"] = t.get("resolved_at")
                t["close_in_seconds"] = None
        return JSONResponse(trades)
    with db.get_conn() as conn:
        # Show trades with real P&L first, then pending. Skip phantom pnl=0 resolved trades.
        # FIX: Include open trades (outcome IS NULL) OR resolved trades with PNL
        rows = conn.execute(
            """SELECT * FROM trades
               WHERE (outcome IS NULL OR pnl IS NOT NULL)
                 AND NOT (outcome IS NOT NULL AND pnl = 0)
               ORDER BY
                   CASE WHEN outcome IS NOT NULL THEN 0 ELSE 1 END,
                   resolved_at DESC, created_at DESC
               LIMIT ?""", (limit,)
        ).fetchall()
        out = []
        now_dt = datetime.now(timezone.utc)
        for r in rows:
            t = dict(r)
            t["amount_display"] = _fmt_amount_usd(t.get("amount"))
            t["pnl_display"] = _fmt_pnl_usd(t.get("pnl")) if t.get("pnl") is not None else None
            mid = t.get("market_id")
            info = close_map.get(mid) if mid else None
            resolves_at = info.get("resolves_at") if info else None
            t["market_resolves_at"] = resolves_at
            if t.get("outcome") is None:
                t["close_at"] = resolves_at
                dt = _parse_iso_utc(resolves_at)
                t["close_in_seconds"] = int((dt - now_dt).total_seconds()) if dt else None
            else:
                t["close_at"] = t.get("resolved_at")
                t["close_in_seconds"] = None
            out.append(t)
        return JSONResponse(out)



@app.get("/api/open-positions")
async def get_open_positions():
    """Return all trades that are still open (outcome is NULL)."""
    import re
    print("DEBUG: /api/open-positions endpoint called")  # Debug print

    def extract_close_time(question: str):
        # Extracts time like "6:10PM-6:15PM" from market question
        match = re.search(r'(\d{1,2}:\d{2}(?:AM|PM))', question)
        return match.group(1) if match else None

    try:
        with db.get_conn() as conn:
            print("DEBUG: Getting database connection")  # Debug print
            rows = conn.execute("""
                SELECT 
                    t.id,
                    t.bot_name,
                    t.market_question,
                    t.side,
                    t.amount,
                    t.shares_bought,
                    t.created_at
                FROM trades t
                WHERE t.outcome IS NULL
                ORDER BY t.created_at DESC
            """).fetchall()
            
            print(f"DEBUG: Found {len(rows)} open positions")  # Debug print
            
            positions = []
            # FIX: Indentação corrigida para estar DENTRO do bloco with
            for r in rows:
                pos = dict(r)
                
                # Formatar o valor investido
                pos['invested'] = _fmt_amount_usd(pos.get('amount'))
                
                # 3. Timestamps
                pos['open_time'] = pos['created_at']
                pos['expected_close_time'] = extract_close_time(pos['market_question'])

                positions.append(pos)
        
        # O retorno também deve ser fora do loop mas dentro da função
        print(f"DEBUG: Returning {len(positions)} positions")  # Debug print
        return JSONResponse(positions)
    except Exception as e:
        print(f"DEBUG: Error in get_open_positions: {e}")  # Debug print
        import traceback
        traceback.print_exc()
        return JSONResponse([])  # Return empty list on error


@app.get("/api/copytrading")
async def get_copytrading():
    from copytrading.copier import TradeCopier
    from copytrading.tracker import WalletTracker
    tracker = WalletTracker()
    copier = TradeCopier(tracker)
    return JSONResponse({
        "wallets": tracker.get_tracked(),
        "stats": copier.get_copy_stats(),
    })


@app.get("/api/earnings")
async def get_earnings():
    with db.get_conn() as conn:
        daily = conn.execute("""
            SELECT date(created_at) as day, COALESCE(SUM(pnl), 0) as pnl,
                   COUNT(*) as trades,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
            FROM trades WHERE outcome IN ('win', 'loss')
            GROUP BY date(created_at) ORDER BY day DESC LIMIT 30
        """).fetchall()

        best = conn.execute(
            "SELECT * FROM trades WHERE pnl IS NOT NULL ORDER BY pnl DESC LIMIT 5"
        ).fetchall()

        worst = conn.execute(
            "SELECT * FROM trades WHERE pnl IS NOT NULL ORDER BY pnl ASC LIMIT 5"
        ).fetchall()

        return JSONResponse({
            "daily": [dict(r) for r in daily],
            "best_trades": [dict(r) for r in best],
            "worst_trades": [dict(r) for r in worst],
        })


@app.get("/api/learning")
async def get_learning():
    active = db.get_active_bots()
    result = {}
    for bot_cfg in active:
        name = bot_cfg["bot_name"]
        result[name] = learning.get_bot_learning_summary(name)
    return JSONResponse(result)


@app.get("/api/open_positions")
async def get_open_positions():
    """Retorna posições abertas com preço atual, TP/SL + % de distância e dados para highlight"""
    mode = config.get_current_mode()
    positions = []
    
    # 1. Obter preços atuais (usando a mesma lógica do código anterior para buscar preços da Simmer)
    price_map = {}
    try:
        api_key = json.load(open(config.SIMMER_API_KEY_PATH))["api_key"]
        markets = _fetch_simmer_markets(api_key, limit=200)
        for m in markets:
            if "id" in m and "current_price" in m:
                try:
                    price_map[m["id"]] = float(m["current_price"])
                except:
                    pass
    except Exception as e:
        print(f"Erro buscando preços: {e}")

    # 2. Buscar trades abertos do DB (usando DB em vez de risk_manager.open_positions pois o dashboard roda em processo separado)
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE outcome IS NULL AND mode=?",
            (mode,)
        ).fetchall()

    for r in rows:
        t = dict(r)
        
        # Dados básicos
        market_id = t.get("market_id")
        shares = float(t.get("shares_bought") or 0)
        amount = float(t.get("amount") or 0)
        entry = amount / shares if shares > 0 else 0
        side = t.get("side", "").upper()
        
        # Determinar Preço Atual
        raw_price = price_map.get(market_id)
        curr = raw_price
        
        # Ajuste de preço para Short (NO)
        if side == "NO" and raw_price is not None:
            curr = 1.0 - raw_price
            
        # Extrair SL/TP
        # Primeiro tenta pegar das colunas do DB (mais rápido/atualizado)
        sl = t.get("current_sl") or t.get("sl_price")
        tp = t.get("current_tp") or t.get("tp_price")
        trailing_enabled = False
        
        # Se não tiver no DB, tenta do trade_features (retrocompatibilidade)
        try:
            feats = json.loads(t.get("trade_features") or "{}")
            if "trailing_enabled" in feats: trailing_enabled = bool(feats["trailing_enabled"])
            
            if sl is None:
                if "sl_price" in feats: sl = float(feats["sl_price"])
                # Fallback percentual
                elif "sl_percent" in feats:
                    sl = entry * (1 + float(feats["sl_percent"])/100)
            
            if tp is None:
                if "tp_price" in feats: tp = float(feats["tp_price"])
                # Fallback percentual
                elif "tp_percent" in feats:
                    tp = entry * (1 + float(feats["tp_percent"])/100)
        except:
            pass

        # Cálculo de distância %
        tp_dist = sl_dist = None
        tp_display = sl_display = "—"

        if tp and curr:
            tp_dist = (tp - curr) / curr * 100
            tp_display = f"{tp:.4f} ({'+' if tp_dist >= 0 else ''}{tp_dist:.2f}%)"
        if sl and curr:
            sl_dist = (curr - sl) / curr * 100
            sl_display = f"{sl:.4f} ({'' if sl_dist >= 0 else ''}{sl_dist:.2f}%)"

        positions.append({
            "trade_id": t.get("trade_id") or str(t.get("id")),
            "bot_name": t.get("bot_name", "unknown"),
            "market": t.get("market_question", "Bitcoin Up or Down..."),
            "side": side,
            "entry": round(entry, 4),
            "current": round(curr, 4) if curr else None,
            "sl_price": sl,
            "tp_price": tp,
            "tp_display": tp_display,
            "sl_display": sl_display,
            "tp_dist": round(tp_dist, 2) if tp_dist is not None else None,
            "sl_dist": round(sl_dist, 2) if sl_dist is not None else None,
            "size_usd": round(amount, 2),
            "trailing": "✅" if trailing_enabled else "❌"
        })

    return {"positions": positions}


if __name__ == "__main__":
    import uvicorn
    print(f"\n🚀 Dashboard rodando em: http://{config.DASHBOARD_HOST}:{config.DASHBOARD_PORT}")
    print(f"👉 Abra o navegador para ver os bots em ação!\n")
    uvicorn.run(app, host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT)
