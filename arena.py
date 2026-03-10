"""Bot Arena Manager — runs 4 competing bots with 4-hour evolution cycles."""

import argparse
import json
import logging
import sys
import time
import random
import math
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
import db
import learning
import edge_model
from core.risk_manager import risk_manager
from bots.bot_momentum import MomentumBot
from bots.bot_mean_rev import MeanRevBot
from bots.bot_sentiment import SentimentBot
from bots.bot_hybrid import HybridBot
from bots.bot_meanrev_sl import MeanRevSLBot
from bots.bot_meanrev_tp import MeanRevTPBot
from bots.bot_orderflow import OrderflowBot
from bots.bot_updown import UpDownBot
from signals.price_feed import get_feed as get_price_feed
from signals.sentiment import get_feed as get_sentiment_feed
from signals.orderflow import get_feed as get_orderflow_feed
from signals.regime_detector import hurst_exponent
from copytrading.tracker import WalletTracker
from copytrading.copier import TradeCopier
from logging_config import setup_logging_with_brt
from evolution_integration import evolution_integration, on_trade_resolved
from bot_evolution_manager import BotEvolutionManager
import telegram_bot
import requests as _req

logger = setup_logging_with_brt("arena", log_file=config.LOG_DIR / "trading-arena.log")

# Wallet sync interval (seconds) - how often to refresh virtual bankroll from Simmer
WALLET_SYNC_INTERVAL = getattr(config, "WALLET_SYNC_INTERVAL", 30)
_last_wallet_sync = 0.0

# Market check interval (seconds)
TRADE_INTERVAL = 60  # Discover markets + place trades every 60s
FAST_POLL_INTERVAL = 0.5  # Poll market prices for SL/TP exits every 0.5s


def create_default_bots():
    """Create the bots from active DB configs (or defaults for first run)."""
    active = db.get_active_bots()
    if active:
        try:
            max_bots = getattr(config, "NUM_BOTS", 5)
            # Prioriza configs mais recentes por geração e created_at
            active = sorted(
                active,
                key=lambda r: (
                    int(r.get("generation", 0) or 0),
                    str(r.get("created_at", "")),
                ),
                reverse=True,
            )[:max_bots]
        except Exception:
            active = active[: getattr(config, "NUM_BOTS", 5)]
        bot_classes = {
            "momentum": MomentumBot,
            "mean_reversion": MeanRevBot,
            "mean_reversion_sl": MeanRevSLBot,
            "mean_reversion_tp": MeanRevTPBot,
            "sentiment": SentimentBot,
            "hybrid": HybridBot,
            "orderflow": OrderflowBot,
            "updown": UpDownBot,
        }
        bots = []
        for cfg in active:
            cls = bot_classes.get(cfg["strategy_type"], MomentumBot)
            params = cfg["params"]
            if isinstance(params, str):
                import json as _j

                params = _j.loads(params)
            bots.append(
                cls(
                    name=cfg["bot_name"],
                    params=params,
                    generation=cfg["generation"],
                    lineage=cfg.get("lineage"),
                )
            )
        if bots:
            return bots

    return [
        MomentumBot(name="momentum-v1", generation=0),
        HybridBot(name="hybrid-v1", generation=0),
        MeanRevBot(name="meanrev-v1", generation=0),
        MeanRevSLBot(name="meanrev-sl-v1", generation=0),
        OrderflowBot(name="orderflow-v1", generation=0),
        UpDownBot(name="updown-rsi-v3", generation=0),  # v3 RSI strategy
    ]


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
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = _req.get(
        f"{config.SIMMER_BASE_URL}/api/sdk/agents/me", headers=headers, timeout=10
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Simmer agents/me failed: {resp.status_code} {resp.text[:200]}"
        )
    data = resp.json()
    bal = data.get("balance")
    try:
        bal = float(bal)
    except (TypeError, ValueError):
        bal = None
    return {"balance": bal, "raw": data}


def create_evolved_bot(winner, loser_type, gen_number):
    """Create an evolved bot based on the winner's influence + loser's strategy.

    Uses the loser strategy's DEFAULT params as a base, copies over any
    shared keys from the winner (e.g. lookback_candles, position_size_pct),
    then mutates. This prevents KeyError when winner and loser have
    different param schemas.
    """
    from bots.bot_momentum import DEFAULT_PARAMS as MOMENTUM_DEFAULTS
    from bots.bot_mean_rev import DEFAULT_PARAMS as MEANREV_DEFAULTS
    from bots.bot_hybrid import DEFAULT_PARAMS as HYBRID_DEFAULTS
    from bots.bot_sentiment import DEFAULT_PARAMS as SENTIMENT_DEFAULTS
    from bots.bot_orderflow import DEFAULT_PARAMS as ORDERFLOW_DEFAULTS
    from bots.bot_updown import DEFAULT_PARAMS as UPDOWN_DEFAULTS

    bot_classes = {
        "momentum": MomentumBot,
        "mean_reversion": MeanRevBot,
        "mean_reversion_sl": MeanRevSLBot,
        "mean_reversion_tp": MeanRevTPBot,
        "sentiment": SentimentBot,
        "hybrid": HybridBot,
        "orderflow": OrderflowBot,
        "updown": UpDownBot,
    }

    default_params_map = {
        "momentum": MOMENTUM_DEFAULTS,
        "mean_reversion": MEANREV_DEFAULTS,
        "mean_reversion_sl": MEANREV_DEFAULTS,
        "mean_reversion_tp": MEANREV_DEFAULTS,
        "sentiment": SENTIMENT_DEFAULTS,
        "hybrid": HYBRID_DEFAULTS,
        "orderflow": ORDERFLOW_DEFAULTS,
        "updown": UPDOWN_DEFAULTS,
    }

    # Start with the target strategy's defaults
    base_params = default_params_map.get(loser_type, MOMENTUM_DEFAULTS).copy()

    # Copy shared keys from winner (transfers learned tuning for common params)
    winner_params = winner.export_params()["params"]
    for key in base_params:
        if key in winner_params:
            base_params[key] = winner_params[key]

    # Mutate
    new_params = winner.mutate(base_params)
    name = f"{loser_type}-g{gen_number}-{random.randint(100, 999)}"

    cls = bot_classes.get(loser_type, MomentumBot)
    return cls(
        name=name,
        params=new_params,
        generation=gen_number,
        lineage=f"{winner.name} -> {name}",
    )


def _validate_bot(bot):
    """Smoke-test a bot by running make_decision with dummy data.
    Returns True if bot can trade, False if it crashes."""
    dummy_market = {"current_price": 0.52, "id": "test", "question": "test"}
    dummy_signals = {"prices": [97000, 97050, 97100], "latest": 97100}
    try:
        result = bot.make_decision(dummy_market, dummy_signals)
        return result.get("action") in ("buy", "skip")
    except Exception as e:
        logger.error(f"  VALIDATION FAILED for {bot.name}: {e}")
        return False


def run_evolution(bots, cycle_number):
    """Run evolution cycle — select survivors, mutate replacements with sample-size guard."""
    logger.info(f"=== Evolution Cycle {cycle_number} ===")
    update_heartbeat()

    # Passa bots ativos para o evolution manager
    evolution_integration.set_active_bots(bots)

    # VERIFICA QUAL TIPO DE EVOLUÇÃO USAR
    if evolution_integration.should_run_regular_evolution():
        logger.info("=== Usando evolução regular (4h) ===")
        return run_regular_evolution(bots, cycle_number)
    else:
        logger.info("=== Usando evolução por trades ===")
        return run_trade_based_evolution(bots, cycle_number)


def run_regular_evolution(bots, cycle_number):
    """Mantém código original de evolução regular"""
    min_trades = getattr(config, "EVOLUTION_MIN_RESOLVED_TRADES", 0) or 0
    diversity_penalty = getattr(config, "DIVERSITY_PENALTY", 0.15)

    # Rank bots by robustness-weighted P&L over the evolution window
    rankings = []
    type_counts = {}
    for bot in bots:
        perf = bot.get_performance(hours=config.EVOLUTION_INTERVAL_HOURS)
        trades = perf.get("total_trades", 0) or 0
        pnl = perf.get("total_pnl", 0) or 0
        win_rate = perf.get("win_rate", 0) or 0
        if min_trades > 0:
            sample_w = min(1.0, math.sqrt(max(0.0, float(trades)) / float(min_trades)))
        else:
            sample_w = 1.0
        score = float(pnl) * sample_w + (float(win_rate) - 0.5) * 2.0 * sample_w
        t = bot.strategy_type
        type_counts[t] = type_counts.get(t, 0) + 1
        penalty = diversity_penalty * max(0, type_counts[t] - 1) / max(1, len(bots))
        score -= penalty
        db.save_generation_snapshot(
            bot.generation,
            bot.name,
            bot.strategy_type,
            win_rate,
            pnl,
            trades,
            bot.strategy_params,
        )
        rankings.append(
            {
                "name": bot.name,
                "strategy_type": bot.strategy_type,
                "generation": bot.generation,
                "pnl": float(pnl),
                "win_rate": float(win_rate),
                "trades": int(trades),
                "score": float(score),
            }
        )

    rankings.sort(key=lambda x: x["score"], reverse=True)
    logger.info("Rankings:")
    for i, r in enumerate(rankings):
        status = "SURVIVES" if i < config.SURVIVORS_PER_CYCLE else "REPLACED"
        logger.info(
            f"  #{i + 1} {r['name']}: score={r['score']:+.2f} P&L=${r['pnl']:.2f}, WR={r['win_rate']:.1%}, Trades={r['trades']} [{status}]"
        )

    survivor_names = {rankings[i]["name"] for i in range(config.SURVIVORS_PER_CYCLE)}
    replaced_names = {
        rankings[i]["name"] for i in range(config.SURVIVORS_PER_CYCLE, len(rankings))
    }

    new_bots = []
    for bot in bots:
        if bot.name in survivor_names:
            bot.reset_daily()
            new_bots.append(bot)

    # Create replacements from winners
    winners = [b for b in bots if b.name in survivor_names]
    replaced = [b for b in bots if b.name in replaced_names]

    for dead_bot in replaced:
        parent = random.choice(winners)
        evolved = create_evolved_bot(parent, dead_bot.strategy_type, cycle_number)

        # Inherit the dead bot's API key slot so evolved bot uses same Simmer account
        if hasattr(dead_bot, "_api_key_slot"):
            evolved._api_key_slot = dead_bot._api_key_slot
            logger.info(
                f"  {evolved.name} inherits slot {dead_bot._api_key_slot} from {dead_bot.name}"
            )

        # Validate the new bot can actually trade before committing
        if not _validate_bot(evolved):
            logger.warning(
                f"  {evolved.name} failed validation, recreating with pure defaults"
            )
            from bots.bot_momentum import DEFAULT_PARAMS as MOMENTUM_DEFAULTS
            from bots.bot_mean_rev import DEFAULT_PARAMS as MEANREV_DEFAULTS
            from bots.bot_hybrid import DEFAULT_PARAMS as HYBRID_DEFAULTS
            from bots.bot_sentiment import DEFAULT_PARAMS as SENTIMENT_DEFAULTS
            from bots.bot_orderflow import DEFAULT_PARAMS as ORDERFLOW_DEFAULTS

            fallback_map = {
                "momentum": MOMENTUM_DEFAULTS,
                "mean_reversion": MEANREV_DEFAULTS,
                "mean_reversion_sl": MEANREV_DEFAULTS,
                "mean_reversion_tp": MEANREV_DEFAULTS,
                "sentiment": SENTIMENT_DEFAULTS,
                "hybrid": HYBRID_DEFAULTS,
                "orderflow": ORDERFLOW_DEFAULTS,
            }
            bot_classes = {
                "momentum": MomentumBot,
                "mean_reversion": MeanRevBot,
                "mean_reversion_sl": MeanRevSLBot,
                "mean_reversion_tp": MeanRevTPBot,
                "sentiment": SentimentBot,
                "hybrid": HybridBot,
                "orderflow": OrderflowBot,
            }
            cls = bot_classes.get(dead_bot.strategy_type, MomentumBot)
            fallback_params = fallback_map.get(
                dead_bot.strategy_type, MOMENTUM_DEFAULTS
            ).copy()
            evolved = cls(
                name=evolved.name,
                params=fallback_params,
                generation=cycle_number,
                lineage=f"{parent.name} -> {evolved.name} (fallback)",
            )
            if hasattr(dead_bot, "_api_key_slot"):
                evolved._api_key_slot = dead_bot._api_key_slot

        db.retire_bot(dead_bot.name)
        db.save_bot_config(
            evolved.name,
            evolved.strategy_type,
            evolved.generation,
            evolved.strategy_params,
            evolved.lineage,
        )

        new_bots.append(evolved)
        logger.info(
            f"  Created {evolved.name} (from {parent.name}): {json.dumps(evolved.strategy_params)[:200]}"
        )

    # Log evolution event
    db.log_evolution(
        cycle_number,
        list(survivor_names),
        list(replaced_names),
        [b.name for b in new_bots if b.name not in survivor_names],
        rankings,
    )

    # Final validation: confirm all bots have API slots and can trade
    for bot in new_bots:
        slot = getattr(bot, "_api_key_slot", None)
        logger.info(
            f"  Post-evolution: {bot.name} ({bot.strategy_type}) slot={slot} params_keys={list(bot.strategy_params.keys())}"
        )

    return new_bots


def run_trade_based_evolution(bots, cycle_number):
    """Nova função que usa o sistema de evolução por trades"""
    logger.info("=== Trade-Based Evolution Cycle ===")

    # Obtém rankings de performance das últimas 6 horas
    rankings = []
    for bot in bots:
        try:
            # Obtém performance do último período (6 horas para ter dados suficientes)
            perf = bot.get_performance(hours=6)
            trades = perf.get("total_trades", 0)
            pnl = perf.get("total_pnl", 0)
            win_rate = perf.get("win_rate", 0)

            # Calcula score ponderado (similar ao regular mas com peso menor)
            sample_weight = min(1.0, trades / 20)  # Peso baseado em trades
            score = (pnl * sample_weight) + ((win_rate - 0.5) * 2.0 * sample_weight)

            rankings.append(
                {
                    "bot": bot,
                    "name": bot.name,
                    "strategy_type": bot.strategy_type,
                    "generation": bot.generation,
                    "pnl": pnl,
                    "win_rate": win_rate,
                    "trades": trades,
                    "score": score,
                }
            )

            logger.info(
                f"  {bot.name}: score={score:+.2f} P&L=${pnl:.2f}, WR={win_rate:.1%}, Trades={trades}"
            )

        except Exception as e:
            logger.error(f"Erro ao analisar {bot.name}: {e}")
            rankings.append(
                {
                    "bot": bot,
                    "name": bot.name,
                    "strategy_type": bot.strategy_type,
                    "generation": bot.generation,
                    "pnl": 0,
                    "win_rate": 0,
                    "trades": 0,
                    "score": -999,
                }
            )

    # Ordena por score decrescente
    rankings.sort(key=lambda x: x["score"], reverse=True)

    # Seleciona sobreviventes (top 3 - mesma lógica do regular)
    survivors_count = getattr(config, "SURVIVORS_PER_CYCLE", 3)
    survivors = rankings[:survivors_count]
    survivor_names = {r["name"] for r in survivors}
    replaced = rankings[survivors_count:]

    logger.info(f"🏆 Sobreviventes: {[s['name'] for s in survivors]}")
    logger.info(f"🔄 Substituídos: {[r['name'] for r in replaced]}")

    # Mantém sobreviventes
    new_bots = []
    for rank in survivors:
        bot = rank["bot"]
        bot.reset_daily()
        new_bots.append(bot)

    # Cria substitutos evoluídos
    for dead_rank in replaced:
        dead_bot = dead_rank["bot"]

        # Seleciona parent (melhor performer entre sobreviventes)
        parent = survivors[0]["bot"]

        # Cria bot evoluído usando função existente
        evolved = create_evolved_bot(parent, dead_bot.strategy_type, cycle_number)

        # Herda slot de API do bot morto
        if hasattr(dead_bot, "_api_key_slot"):
            evolved._api_key_slot = dead_bot._api_key_slot
            logger.info(
                f"  {evolved.name} herda slot {dead_bot._api_key_slot} de {dead_bot.name}"
            )

        # Valida novo bot com limite de tentativas
        max_retries = 3
        retry_count = 0
        while not _validate_bot(evolved) and retry_count < max_retries:
            logger.warning(
                f"  {evolved.name} falhou validação (tentativa {retry_count + 1}/{max_retries}), recriando com defaults"
            )
            # Recria com parâmetros padrão se falhar
            evolved = create_evolved_bot(parent, dead_bot.strategy_type, cycle_number)
            if hasattr(dead_bot, "_api_key_slot"):
                evolved._api_key_slot = dead_bot._api_key_slot
            retry_count += 1

        # Se ainda falhar após todas as tentativas, usa um bot padrão simples
        if not _validate_bot(evolved):
            logger.error(
                f"  {evolved.name} falhou validação após {max_retries} tentativas, criando bot padrão"
            )
            # Cria um bot básico do mesmo tipo mas com parâmetros mínimos
            from bots.bot_momentum import DEFAULT_PARAMS as MOMENTUM_DEFAULTS
            from bots.bot_mean_rev import DEFAULT_PARAMS as MEANREV_DEFAULTS
            from bots.bot_hybrid import DEFAULT_PARAMS as HYBRID_DEFAULTS

            default_params = {
                "momentum": MOMENTUM_DEFAULTS,
                "mean_reversion": MEANREV_DEFAULTS,
                "mean_reversion_sl": MEANREV_DEFAULTS,
                "mean_reversion_tp": MEANREV_DEFAULTS,
                "hybrid": HYBRID_DEFAULTS,
            }.get(dead_bot.strategy_type, MOMENTUM_DEFAULTS)

            # Cria bot com parâmetros mínimos
            evolved = create_evolved_bot(parent, dead_bot.strategy_type, cycle_number)
            evolved.strategy_params = default_params.copy()
            if hasattr(dead_bot, "_api_key_slot"):
                evolved._api_key_slot = dead_bot._api_key_slot

        # Registra mudanças no banco
        db.retire_bot(dead_bot.name)
        db.save_bot_config(
            evolved.name,
            evolved.strategy_type,
            evolved.generation,
            evolved.strategy_params,
            evolved.lineage,
        )

        new_bots.append(evolved)
        logger.info(f"  ⭐ Criado {evolved.name} (de {parent.name})")

    # Registra evento de evolução com trigger reason
    db.log_evolution(
        cycle_number,
        [s["name"] for s in survivors],
        [r["name"] for r in replaced],
        [b.name for b in new_bots if b.name not in survivor_names],
        rankings,
        trigger_reason="trade_threshold",  # Indica que foi por trades
    )

    logger.info(f"✅ Evolução por trades concluída")
    return new_bots


def load_api_key():
    try:
        with open(config.SIMMER_API_KEY_PATH) as f:
            return json.load(f).get("api_key")
    except FileNotFoundError:
        logger.error(f"No API key at {config.SIMMER_API_KEY_PATH}")
        return None


def discover_markets(api_key):
    """Find active price-action markets: BTC, ETH, SOL, XRP, DOGE, CPI, interest rates.

    Exclusion rules:
    - Skips subjective markets (politics, sports, culture) via config.EXCLUDED_MARKET_KEYWORDS.
    - Skips markets expiring within 20 minutes (hard safety floor).
    - Applies liquidity and spread filters from config.
    """
    import requests
    from datetime import datetime, timezone

    _MIN_EXPIRY_SECONDS = 25 * 60  # 25-minute hard floor (Sniper Mode)

    # Asset tag definitions: (tag_label, list_of_matching_terms_in_question)
    ASSET_TAGS = [
        ("btc",   ["btc", "bitcoin"]),
        ("eth",   ["eth", "ethereum"]),
        ("sol",   ["sol", "solana"]),
        ("xrp",   ["xrp", "ripple"]),
        ("doge",  ["doge", "dogecoin"]),
        ("macro", ["cpi", "interest rate", "fed rate", "inflation", "fomc", "fed funds"]),
    ]

    markets = []
    found_counts = {tag: 0 for tag, _ in ASSET_TAGS}

    # Subjective exclusion list from config (lowercase)
    excluded_kws = [kw.lower() for kw in getattr(config, "EXCLUDED_MARKET_KEYWORDS", [])]

    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        resp = requests.get(
            f"{config.SIMMER_BASE_URL}/api/sdk/markets",
            headers=headers,
            params={"status": "active", "limit": 200},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.error(f"Market discovery HTTP {resp.status_code}")
            return markets

        data = resp.json()
        markets_list = data if isinstance(data, list) else data.get("markets", [])

        for m in markets_list:
            q = m.get("question", "").lower()

            # ── 1. Must contain at least one price-action trigger keyword ──────────
            if not any(kw in q for kw in config.TARGET_MARKET_KEYWORDS):
                continue

            # ── 2. Hard exclusion: subjective / non-price-action markets ──────────
            if any(excl in q for excl in excluded_kws):
                continue

            # ── 3. Must belong to at least one tracked asset category ─────────────
            matched_tag = None
            for tag, terms in ASSET_TAGS:
                if any(term in q for term in terms):
                    matched_tag = tag
                    break
            if matched_tag is None:
                continue  # not a tracked asset, skip

            # ── 4. Expiry guard: drop markets closing within 20 minutes ───────────
            end_ts = None
            resolves_at = m.get("resolves_at")
            if resolves_at:
                try:
                    end_ts = datetime.fromisoformat(
                        str(resolves_at).replace("Z", "+00:00")
                    )
                except Exception:
                    end_ts = None

            if end_ts is None:
                # Fallback: try ISO datetime embedded in question text
                try:
                    import re
                    match = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", q)
                    if match:
                        end_ts = datetime.fromisoformat(match.group(0))
                except Exception:
                    end_ts = None

            if end_ts is not None:
                try:
                    nowdt = datetime.now(timezone.utc)
                    if end_ts.tzinfo is None:
                        end_ts = end_ts.replace(tzinfo=timezone.utc)
                    tte = (end_ts - nowdt).total_seconds()
                    if tte < _MIN_EXPIRY_SECONDS:
                        continue  # too close to expiry
                except Exception:
                    pass

            # ── 5. Liquidity filter ───────────────────────────────────────────────
            liq_field = m.get("liquidity")
            if liq_field is not None:
                try:
                    if float(liq_field) < config.MARKET_FILTER.get("min_liquidity_usd", 0):
                        continue
                except Exception:
                    pass

            # ── 6. Spread filter ──────────────────────────────────────────────────
            try:
                bb = float(m.get("best_bid") or 0)
                ba = float(m.get("best_ask") or 0)
                if bb > 0 and ba > 0:
                    mid_price = (bb + ba) / 2
                    spread_pct = (ba - bb) / mid_price * 100
                    if spread_pct > config.get_max_spread_allowed():
                        continue
            except Exception:
                pass

            # ── Passed all filters → add market ──────────────────────────────────
            markets.append(m)
            found_counts[matched_tag] += 1

    except Exception as e:
        logger.error(f"Market discovery error: {e}")

    logger.info(
        "Discovered markets — "
        + ", ".join(f"{tag.upper()}: {n}" for tag, n in found_counts.items())
        + f" | Total: {len(markets)}"
    )
    return markets


def is_5min_market(question):
    """Check if this is a strict 5-minute window market (not 15-min or hourly)."""
    import re

    q = question.lower()
    # Match patterns like "10:00PM-10:05PM" (5-min range)
    range_match = re.search(r"(\d{1,2}):(\d{2})(am|pm)-(\d{1,2}):(\d{2})(am|pm)", q)
    if range_match:
        h1, m1 = int(range_match.group(1)), int(range_match.group(2))
        h2, m2 = int(range_match.group(4)), int(range_match.group(5))
        ap1, ap2 = range_match.group(3), range_match.group(6)
        # Convert to 24h
        if ap1 == "pm" and h1 != 12:
            h1 += 12
        if ap1 == "am" and h1 == 12:
            h1 = 0
        if ap2 == "pm" and h2 != 12:
            h2 += 12
        if ap2 == "am" and h2 == 12:
            h2 = 0
        diff = (h2 * 60 + m2) - (h1 * 60 + m1)
        if diff < 0:
            diff += 24 * 60
        return diff == 5
    return False


def _parse_resolves_at(value):
    if not value:
        return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        if " " in s and "T" not in s:
            s = s.replace(" ", "T", 1)
        # Python's datetime.fromisoformat supports up to microseconds (6 digits).
        # Some APIs return nanoseconds; truncate fractional seconds to 6 digits.
        if "." in s:
            head, tail = s.split(".", 1)
            off_pos = tail.find("+")
            if off_pos == -1:
                off_pos = tail.find("-")
            if off_pos != -1:
                frac = tail[:off_pos]
                offset = tail[off_pos:]
                if len(frac) > 6:
                    frac = frac[:6]
                s = f"{head}.{frac}{offset}"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            # Treat naive timestamps as UTC
            dt = (
                dt.replace(tzinfo=timezone.utc)
                .astimezone(timezone.utc)
                .replace(tzinfo=None)
            )
        return dt
    except Exception:
        return None


def _parse_question_end_time_utc(question: str):
    if not question or not isinstance(question, str):
        return None
    import re

    q = question
    m = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}).*?(\d{1,2})(?::(\d{2}))?(AM|PM)\s*-\s*(\d{1,2})(?::(\d{2}))?(AM|PM)\s*ET",
        q,
        flags=re.IGNORECASE,
    )
    if not m:
        return None

    month_name = m.group(1).lower()
    day = int(m.group(2))
    h2 = int(m.group(6))
    m2 = int(m.group(7) or "0")
    ap2 = (m.group(8) or "").upper()
    if ap2 == "PM" and h2 != 12:
        h2 += 12
    if ap2 == "AM" and h2 == 12:
        h2 = 0

    months = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    month = months.get(month_name)
    if not month:
        return None

    now_utc = datetime.now(timezone.utc)
    year = now_utc.year

    # Assume ET is EST in Feb; fixed UTC-5 (good enough for this arena's BTC 5-min markets)
    dt_et_naive = datetime(year, month, day, h2, m2, 0)
    dt_utc = dt_et_naive + timedelta(hours=5)

    # If we parsed something that ended way in the past, it might be next year (rare edge case)
    if (dt_utc - now_utc.replace(tzinfo=None)).total_seconds() < -6 * 60 * 60:
        dt_et_naive = datetime(year + 1, month, day, h2, m2, 0)
        dt_utc = dt_et_naive + timedelta(hours=5)

    return dt_utc


def is_5min_market_obj(market: dict) -> bool:
    q = (market.get("question") or "").lower()
    has_price_target = any(kw in q for kw in config.TARGET_MARKET_KEYWORDS)
    if not has_price_target:
        return False

    dt = _parse_resolves_at(market.get("resolves_at"))
    if dt:
        tte = (dt - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()
        return config.TRADE_MIN_TTE_SECONDS <= tte <= config.TRADE_MAX_TTE_SECONDS

    question = market.get("question", "")
    if not is_5min_market(question):
        return False

    end_dt = _parse_question_end_time_utc(question)
    if not end_dt:
        return False

    tte = (end_dt - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()
    return config.TRADE_MIN_TTE_SECONDS <= tte <= config.TRADE_MAX_TTE_SECONDS


def expire_stale_trades():
    """Expire trades that are stale (adaptive):
    - If resolved trades in last 24h < 80 => expire trades older than 1 hour
    - Else expire trades older than 2 hours
    This prevents stale trades from clogging queue during low-data regimes.
    """
    try:
        resolved = db.get_global_resolved_trades_count(hours=24)
    except Exception:
        resolved = 0

    expiry_hours = 1 if resolved < 80 else 2
    with db.get_conn() as conn:
        count = conn.execute(f"""
            UPDATE trades SET outcome = 'expired', pnl = 0, resolved_at = datetime('now')
            WHERE outcome IS NULL AND created_at < datetime('now', '-{expiry_hours} hours')
        """).rowcount
    if count > 0:
        logger.info(f"Expired {count} stale trades (older than {expiry_hours}h)")
    return count


def resolve_trades(api_key):
    """Check Simmer for resolved markets and update trade outcomes."""
    import requests

    try:
        headers = {"Authorization": f"Bearer {api_key}"}

        # Get pending trades from our DB
        with db.get_conn() as conn:
            pending = conn.execute(
                "SELECT id, market_id, bot_name, side, amount, shares_bought, trade_features, reasoning FROM trades WHERE outcome IS NULL"
            ).fetchall()

        if not pending:
            return 0

        # Get unique market IDs we need to check
        market_ids = list({t["market_id"] for t in pending})

        # Fetch resolved markets from Simmer
        resp = requests.get(
            f"{config.SIMMER_BASE_URL}/api/sdk/markets",
            headers=headers,
            params={"status": "resolved", "limit": 200},
            timeout=15,
        )
        if resp.status_code != 200:
            return 0

        data = resp.json()
        markets_list = data if isinstance(data, list) else data.get("markets", [])

        # Build lookup: market_id -> market with outcome
        resolved_map = {}
        for m in markets_list:
            mid = m.get("id") or m.get("market_id")
            if mid in market_ids:
                resolved_map[mid] = m

        if not resolved_map:
            return 0

        count = 0
        for trade in pending:
            market_id = trade["market_id"]
            if market_id not in resolved_map:
                continue

            market = resolved_map[market_id]
            # outcome field: true = YES won, false = NO won
            market_outcome = market.get("outcome")
            if market_outcome is None:
                continue

            side = trade["side"]
            amount = trade["amount"]
            try:
                shares = trade["shares_bought"] or 0
            except (IndexError, KeyError):
                shares = 0

            # Did this bot's voted side win?
            if side.lower() == "yes":
                won = market_outcome is True
            else:
                won = market_outcome is False

            outcome = "win" if won else "loss"

            # P&L: win = shares pay $1 each minus cost; loss = lose entire cost
            if shares > 0:
                pnl = (shares - amount) if won else -amount
            else:
                pnl = 0  # This bot voted but wasn't the executor

            db.resolve_trade(trade["id"], outcome, pnl)

            # NOTIFICA SISTEMA DE EVOLUÇÃO sobre trade resolvido
            trade_result = {
                "market_id": market_id,
                "side": side,
                "outcome": outcome,
                "pnl": pnl,
                "shares": shares,
                "won": won,
            }
            on_trade_resolved(trade["bot_name"], trade_result)

            # Learn from outcome using features captured AT TRADE TIME (not resolution time)
            try:
                stored_features = trade["trade_features"]
                if stored_features:
                    features = json.loads(stored_features)
                else:
                    # Fallback: extract features from reasoning text
                    try:
                        reasoning = trade["reasoning"]
                    except (KeyError, IndexError):
                        reasoning = None
                    features = learning.extract_features_from_reasoning(reasoning)
            except (KeyError, json.JSONDecodeError):
                features = None

            if features:
                learning.record_outcome(trade["bot_name"], features, side, won)

            try:
                stored = trade["trade_features"]
                payload = json.loads(stored) if stored else None
                if (
                    isinstance(payload, dict)
                    and isinstance(payload.get("x"), dict)
                    and shares > 0
                ):
                    mp = payload.get("market_price", market.get("current_price", 0.5))
                    try:
                        mp = float(mp)
                    except (TypeError, ValueError):
                        mp = market.get("current_price", 0.5)
                    y_yes = 1 if market_outcome is True else 0
                    edge_model.update_model(trade["bot_name"], mp, payload["x"], y_yes)
            except Exception:
                pass

            count += 1

        if count > 0:
            logger.info(
                f"Resolved {count} trades ({sum(1 for t in pending if resolved_map.get(t['market_id']))} pending matched {len(resolved_map)} resolved markets)"
            )
        return count

    except Exception as e:
        logger.error(f"Trade resolution error: {e}")
        return 0


def load_bot_keys():
    """Load per-bot API keys. Returns dict of bot_name -> api_key."""
    try:
        with open(config.SIMMER_BOT_KEYS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def assign_bot_slots(bots, bot_keys, default_key):
    """Assign each bot to a Simmer account slot.

    Slots are named: slot_0, slot_1, slot_2, slot_3
    Each slot maps to a Simmer API key. When a bot is replaced during
    evolution, the new bot inherits the dead bot's slot (and API key).
    Bots that already have a slot (from evolution inheritance) keep it.
    """
    all_slots = ["slot_0", "slot_1", "slot_2", "slot_3"]

    # First pass: collect already-assigned slots
    used_slots = set()
    for bot in bots:
        if hasattr(bot, "_api_key_slot") and bot._api_key_slot:
            used_slots.add(bot._api_key_slot)

    # Second pass: assign free slots to bots that don't have one
    free_slots = [s for s in all_slots if s not in used_slots]
    for bot in bots:
        if not hasattr(bot, "_api_key_slot") or not bot._api_key_slot:
            if free_slots:
                bot._api_key_slot = free_slots.pop(0)
            else:
                bot._api_key_slot = all_slots[0]  # fallback

    for bot in bots:
        key = bot_keys.get(bot._api_key_slot, default_key)
        logger.info(f"  {bot.name} -> {bot._api_key_slot} (key: ...{key[-8:]})")


def restore_open_positions():
    """Lê todas as trades abertas do DB e as restaura no RiskManager para monitoramento."""
    from core.position import OpenPosition

    try:
        with db.get_conn() as conn:
            # Seleciona as colunas necessárias para reconstruir o estado
            rows = conn.execute("""
                SELECT 
                    id, bot_name, market_id, side, amount, shares_bought, 
                    current_sl, current_tp, tp_triggered, trade_features, reasoning, created_at, owner_tag, trade_id
                FROM trades
                WHERE outcome IS NULL
            """).fetchall()

            restored = 0
            for r in rows:
                features = {}
                if r["trade_features"]:
                    try:
                        features = (
                            json.loads(r["trade_features"])
                            if isinstance(r["trade_features"], str)
                            else r["trade_features"]
                        )
                    except:
                        pass

                # Reconstruir objeto OpenPosition
                # Note: entry_price é recalculado se não salvo explicitamente
                shares = r["shares_bought"] or 0
                entry_price = (r["amount"] / shares) if shares > 0 else 0

                # Se entry_price for 0, o RiskManager pode ignorar a posição, então tentamos ser conservadores
                if entry_price <= 0:
                    continue

                pos = OpenPosition(
                    market_id=r["market_id"],
                    bot_name=r["bot_name"],
                    direction=r["side"],
                    entry_price=entry_price,
                    size_usd=r["amount"],
                    entry_time=time.time(),  # Time do restart
                    sl_price=r["current_sl"] or r.get("sl_price"),
                    tp_price=r["current_tp"] or r.get("tp_price"),
                    trade_id=r["trade_id"],  # FIXED: Use the Simmer transaction ID
                    id=r["id"],  # FIXED: Use the database row ID
                    owner_tag=r["owner_tag"], # Restore owner_tag
                    shares=shares,
                    tp_triggered=bool(r["tp_triggered"]),
                    # Se TP já triggou, trailing deve estar ligado
                    trailing_enabled=bool(
                        r["tp_triggered"] or features.get("trailing_enabled", False)
                    ),
                    trailing_distance=features.get("trailing_distance", 0.025),
                    trailing_step=features.get("trailing_step", 0.01),
                )

                risk_manager.add_position(pos)
                restored += 1

            if restored > 0:
                logger.info(
                    f"Monitor: Restauradas {restored} posições abertas do DB para monitoramento ativo."
                )
    except Exception as e:
        logger.error(f"Erro ao restaurar posições: {e}")
        import traceback

        traceback.print_exc()


class PositionMonitorThread(threading.Thread):
    """Background thread that checks SL/TP via RiskManager every 15s."""

    def __init__(self, api_key):
        super().__init__(daemon=True, name="position-monitor")
        self.api_key = api_key
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        """Main monitor loop — polls every FAST_POLL_INTERVAL."""
        logger.info(
            f"Position monitor started (checking SL/TP every {FAST_POLL_INTERVAL}s)"
        )

        while not self._stop_event.is_set():
            try:
                # 0. Skip if no open positions
                if not risk_manager.open_positions:
                    time.sleep(FAST_POLL_INTERVAL)
                    continue

                # 1. Fetch prices
                market_prices = self._fetch_market_prices()

                if market_prices:
                    # 2. Check Global Daily PnL (Realized + Floating)
                    realized_net = db.get_daily_net_pnl(risk_manager.mode)
                    floating_pnl = risk_manager.get_floating_pnl(market_prices)
                    total_pnl_daily = realized_net + floating_pnl
                    
                    limit = risk_manager.limits.get("max_daily_loss_global", 0)
                    if total_pnl_daily <= -limit and limit > 0:
                        risk_manager._handle_global_stop(abs(total_pnl_daily), limit)
                        # No monitor cycle, just sleep
                        time.sleep(FAST_POLL_INTERVAL)
                        continue

                    # 3. Check SL/TP via RiskManager
                    exits = risk_manager.check_sl_tp(market_prices)

                    if exits:
                        logger.info(f"Monitor: Found {len(exits)} positions to close.")

                        # 4. Execute exits
                        for pos, reason, current_price in exits:
                            risk_manager.close_position(pos, reason, current_price)

                time.sleep(FAST_POLL_INTERVAL)

            except Exception as e:
                logger.error(f"Position monitor error: {e}")
                time.sleep(FAST_POLL_INTERVAL)

    def _fetch_market_prices(self):
        """Fetch current prices for all active markets from Simmer."""
        import requests

        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            resp = requests.get(
                f"{config.SIMMER_BASE_URL}/api/sdk/markets",
                headers=headers,
                params={"status": "active", "limit": 200},
                timeout=10,
            )
            if resp.status_code != 200:
                return {}
            data = resp.json()
            markets_list = data if isinstance(data, list) else data.get("markets", [])

            # Map ID -> Data
            price_map = {}
            for m in markets_list:
                mid = m.get("id") or m.get("market_id")
                if mid:
                    price_map[mid] = m
            return price_map
        except Exception:
            return {}


def update_heartbeat():
    """Updates the watchdog heartbeat file."""
    try:
        with open(".heartbeat", "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass


def main_loop(bots, api_key):
    """Main trading loop — each bot trades independently on its own Simmer account."""
    price_feed = get_price_feed()
    sentiment_feed = get_sentiment_feed()
    orderflow_feed = get_orderflow_feed()

    price_feed.start()
    sentiment_feed.start()
    orderflow_feed.start()

    # Start Position Monitor (Background SL/TP Check)
    restore_open_positions()  # Restaura o que já estava aberto ANTES de ligar o monitor
    monitor = PositionMonitorThread(api_key)
    monitor.start()

    evolution_interval = config.EVOLUTION_INTERVAL_HOURS * 3600

    # Restore evolution state from DB so it survives restarts
    saved_cycle = db.get_arena_state("evolution_cycle", "0")
    cycle_number = int(saved_cycle)
    saved_last_evo = db.get_arena_state("last_evolution_time")
    if saved_last_evo:
        last_evolution = float(saved_last_evo)
        elapsed = time.time() - last_evolution
        logger.info(
            f"Restored evolution timer: cycle {cycle_number}, {elapsed / 3600:.1f}h since last evolution"
        )
    else:
        last_evolution = time.time()
        # Persist the initial start so it survives restarts before first evolution
        db.set_arena_state("last_evolution_time", str(last_evolution))
        db.set_arena_state("evolution_cycle", "0")
        logger.info("No saved evolution state, starting fresh timer (persisted)")

    executed = set()
    skip_cache = {}
    skip_retry = getattr(config, "SKIP_RETRY_SECONDS", 45) or 45
    with db.get_conn() as conn:
        recent = conn.execute(
            "SELECT bot_name, market_id FROM trades WHERE created_at >= datetime('now', '-3 hours', '-4 hours')"
        ).fetchall()
        for r in recent:
            executed.add((r["bot_name"], r["market_id"]))
    logger.info(
        f"Loaded {len(executed)} recent executed trade keys from DB (dedup across restarts)"
    )

    # Load per-bot API keys and assign slots
    bot_keys = load_bot_keys()
    assign_bot_slots(bots, bot_keys, api_key)
    multi_account = len(bot_keys) >= config.NUM_BOTS
    if multi_account:
        logger.info(f"Multi-account mode: {len(bot_keys)} Simmer accounts loaded")
    else:
        logger.info(
            f"Single-account mode: {len(bot_keys)} bot keys found (need {config.NUM_BOTS} for independent trading)"
        )

    logger.info(
        f"Arena started with {len(bots)} bots in {config.get_current_mode()} mode"
    )

    # === Log de Configurações ===
    logger.info("=== Configurações Ativas ===")
    # Log aggression mode clearly
    try:
        agg = (
            config.get_aggression_level()
            if hasattr(config, "get_aggression_level")
            else "medium"
        )
        if agg == "aggressive":
            logger.info(
                "🔴 MODO AGGRESSIVE ATIVO - MIN_CONFIDENCE=0.48, MIN_EDGE=0.12%, TRADES MÁXIMOS=150+/dia"
            )
        else:
            logger.info(
                f"Modo: {agg.title()} - MIN_CONFIDENCE={config.get_min_confidence() if hasattr(config, 'get_min_confidence') else 0.55:.2f}"
            )
    except Exception:
        pass
    logger.info(
        f"Janela de Mercado: {config.MARKET_FILTER['min_window_seconds'] / 3600:.1f}h a {config.MARKET_FILTER['max_window_seconds'] / 3600:.1f}h"
    )
    logger.info(
        f"Janela Fallback: > {config.MARKET_FILTER['fallback_min_seconds'] / 60:.0f} min (Se permitido)"
    )
    logger.info(f"Posição Padrão: {config.POSITION_SIZE_PCT:.1%} do bankroll")
    logger.info("============================")

    # === Persist Bot Configs to DB on Startup ===
    try:
        logger.info(f"Bots em memória: {[b.name for b in bots]}")
        existing_bots = set(db.get_active_bot_names())
        logger.info(f"Bots já no DB: {list(existing_bots)}")

        saved_count = 0
        for bot in bots:
            # Sempre tenta atualizar ou inserir para garantir que os parâmetros estejam sincronizados
            try:
                # Se o bot já existe, o save_bot_config pode falhar se não tratarmos updates
                # Mas a função db.save_bot_config faz INSERT.
                # Vamos verificar se ele já existe antes de inserir para evitar duplicação ou erro
                if bot.name not in existing_bots:
                    db.save_bot_config(
                        bot_name=bot.name,
                        strategy_type=bot.strategy_type,
                        generation=bot.generation,
                        params=bot.strategy_params,
                        lineage=bot.lineage,
                    )
                    logger.info(
                        f"Bot salvo no DB: {bot.name} ({bot.strategy_type}, gen={bot.generation})"
                    )
                    saved_count += 1
                else:
                    logger.debug(f"Bot {bot.name} já existe no DB, pulando inserção.")
            except Exception as e:
                logger.error(f"Erro ao salvar bot {bot.name} no DB: {e}")

        if saved_count > 0:
            logger.info(f"{saved_count} bot(s) salvos no banco de dados.")
        else:
            logger.info("Nenhum novo bot precisou ser salvo no DB.")

    except Exception as e:
        logger.error(f"Erro crítico ao persistir bots no DB: {e}")

    logger.info(f"Bots: {[b.name for b in bots]}")
    logger.info(f"Evolution every {config.EVOLUTION_INTERVAL_HOURS}h")

    # Inicializar RiskManager com a bankroll atual
    try:
        # Usar o helper dinâmico que considera PnL + Banca Inicial
        bankroll = float(db.get_total_current_capital(config.get_current_mode()))
        risk_manager.update_bankroll(bankroll)
        logger.info(f"RiskManager inicializado com bankroll dinâmico: ${bankroll:.2f}")
    except Exception as e:
        logger.warning(
            f"Não foi possível obter bankroll dinâmico, usando fallback: {e}"
        )
        risk_manager.update_bankroll(config.PAPER_STARTING_BALANCE)

    # Timer para logs periódicos (15min)
    last_status_log = 0
    STATUS_LOG_INTERVAL = 900  # 15 minutos

    while True:
        try:
            # === Watchdog Heartbeat ===
            update_heartbeat()

            # === Log Periódico de Status (15min) ===
            if time.time() - last_status_log > STATUS_LOG_INTERVAL:
                try:
                    status = evolution_integration.get_evolution_status()
                    current_trades = status.get("global_trade_count", 0)
                    target_trades = status.get("target_trades", 100)
                    remaining = max(0, target_trades - current_trades)

                    # Usando cores se disponível (importado do risk_manager ou definido aqui)
                    # Como não temos Colors importado aqui, vamos usar log simples mas formatado
                    logger.info("=" * 50)
                    logger.info(f"🧬 STATUS DA EVOLUÇÃO")
                    logger.info(
                        f"📊 Trades Resolvidos: {current_trades}/{target_trades}"
                    )
                    logger.info(f"⏳ Faltam: {remaining} trades para próxima evolução")

                    if status.get("cooldown_active"):
                        logger.info(f"🔒 Cooldown Ativo: Sim")

                    logger.info("=" * 50)
                    last_status_log = time.time()
                except Exception as e:
                    logger.warning(f"Erro ao logar status de evolução: {e}")

            # === REGISTRA BOTS ATIVOS PARA EVOLUÇÃO ===
            evolution_integration.set_active_bots(bots)

            # === EVOLUTION CHECK (agora com guarda de volume mínimo) ===
            total_resolved = 0
            with db.get_conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE resolved_at IS NOT NULL AND created_at >= datetime('now', ?)",
                    (f"-{config.EVOLUTION_INTERVAL_HOURS} hours",),
                ).fetchone()
                total_resolved = row[0] if row else 0

            # Dynamic skip retry: reduce cooldown while we have limited resolved trades
            try:
                base_skip = getattr(config, "SKIP_RETRY_SECONDS", 45) or 45
                if total_resolved < 80:
                    skip_retry = max(10, int(base_skip / 2))
                else:
                    skip_retry = base_skip
            except Exception:
                skip_retry = getattr(config, "SKIP_RETRY_SECONDS", 45) or 45

            if (time.time() - last_evolution >= evolution_interval) and (
                total_resolved >= config.EVOLUTION_MIN_RESOLVED_TRADES
            ):
                cycle_number += 1
                bots = run_evolution(bots, cycle_number)
                last_evolution = time.time()
                # Persist evolution state so it survives restarts
                db.set_arena_state("evolution_cycle", str(cycle_number))
                db.set_arena_state("last_evolution_time", str(last_evolution))
                skip_cache.clear()

                # Reset daily losses and resume trading after evolution
                logger.info(
                    f"Resetting daily losses and resuming bots after evolution cycle {cycle_number}"
                )
                db.reset_arena_day(mode=config.get_current_mode())
                # Reset do RiskManager também
                risk_manager.reset_daily()

                # Ensure all bots are unpaused and ready to trade
                for bot in bots:
                    bot.reset_daily()
                    logger.info(f"Bot {bot.name} resumed trading after evolution")

                # Re-assign slots — new bots inherit the killed bot's slot index
                assign_bot_slots(bots, bot_keys, api_key)
            elif time.time() - last_evolution >= evolution_interval:
                logger.info(
                    f"Waiting for enough data... only {total_resolved}/{config.EVOLUTION_MIN_RESOLVED_TRADES} trades resolved"
                )

            if multi_account:
                for slot_key in set(bot_keys.values()):
                    resolve_trades(slot_key)
                    update_heartbeat()
            else:
                resolve_trades(api_key)
                update_heartbeat()

            # === Wallet Sync: periodically fetch Simmer balances and update RiskManager ===
            try:
                now = time.time()
                if now - _last_wallet_sync > WALLET_SYNC_INTERVAL:
                    _last_wallet_sync = now
                    if config.get_current_mode() == "paper":
                        keys = _load_simmer_api_keys()
                        if keys:
                            total = 0.0
                            for k in keys:
                                try:
                                    info = _fetch_simmer_balance(k)
                                    bal = info.get("balance")
                                    if bal is not None:
                                        total += float(bal)
                                except Exception as be:
                                    logger.warning(
                                        f"Wallet fetch failed for a key: {be}"
                                    )

                            # Persist and update RiskManager only if we obtained a valid total
                            if total > 0:
                                # Store in DB for shared visibility
                                try:
                                    db.set_arena_state("virtual_bankroll", str(total))
                                except Exception:
                                    logger.debug(
                                        "Failed to persist virtual_bankroll to DB"
                                    )
                                try:
                                    risk_manager.update_bankroll(total)
                                    logger.info(
                                        f"RiskManager bankroll synced from Simmer: ${total:.2f}"
                                    )
                                except Exception as re:
                                    logger.warning(
                                        f"Failed to update RiskManager bankroll: {re}"
                                    )
                        else:
                            logger.debug("No Simmer API keys found for wallet sync")

            except Exception as e:
                logger.debug(f"Wallet sync error: {e}")

            # Clean up stale trades that fell off the resolved API
            expire_stale_trades()

            # VERIFICA SE SISTEMA DE EVOLUÇÃO POR TRADES PRECISA EXECUTAR
            # (agora com bots ativos registrados)
            evolution_integration.check_and_trigger_evolution_if_needed()

            # Discover active markets (any key works for read-only)
            update_heartbeat()
            markets = discover_markets(api_key)
            update_heartbeat()
            if not markets:
                logger.debug("No active 5-min markets found, waiting...")
                # Position monitor thread handles SL/TP independently
                time.sleep(30)
                continue

            # Filter to strict 5-minute window markets only
            five_min_markets = [m for m in markets if is_5min_market_obj(m)]
            if not five_min_markets:
                now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
                min_tte = None
                max_tte = None
                earliest_close = None
                latest_close = None
                has_close = 0
                in_window = 0
                for m in markets:
                    close_dt = _parse_resolves_at(m.get("resolves_at"))
                    if not close_dt:
                        close_dt = _parse_question_end_time_utc(m.get("question", ""))
                    if not close_dt:
                        continue
                    has_close += 1
                    tte = (close_dt - now_dt).total_seconds()
                    if min_tte is None or tte < min_tte:
                        min_tte = tte
                        earliest_close = close_dt
                    if max_tte is None or tte > max_tte:
                        max_tte = tte
                        latest_close = close_dt
                    if (
                        config.TRADE_MIN_TTE_SECONDS
                        <= tte
                        <= config.TRADE_MAX_TTE_SECONDS
                    ):
                        in_window += 1

                sample = (markets[0].get("question") or "")[:120] if markets else ""
                if has_close:
                    logger.info(
                        f"Found {len(markets)} BTC markets but none matched strict 5-min window filter "
                        f"(window={config.TRADE_MIN_TTE_SECONDS}-{config.TRADE_MAX_TTE_SECONDS}s, in_window={in_window}/{has_close}, "
                        f"earliest_close_utc={earliest_close.isoformat() if earliest_close else '-'}, "
                        f"latest_close_utc={latest_close.isoformat() if latest_close else '-'}, "
                        f"min_tte_s={int(min_tte) if min_tte is not None else '-'}, max_tte_s={int(max_tte) if max_tte is not None else '-'}, "
                        f"sample='{sample}')"
                    )
                else:
                    logger.info(
                        f"Found {len(markets)} BTC markets but none matched strict 5-min window filter "
                        f"(window={config.TRADE_MIN_TTE_SECONDS}-{config.TRADE_MAX_TTE_SECONDS}s, no_close_times, sample='{sample}')"
                    )
                time.sleep(30)
                continue
            logger.info(
                f"Trading on {len(five_min_markets)} markets this cycle (of {len(markets)} discovered)"
            )

            # Gather signals for multiple crypto markets
            # Detect crypto type from market question
            def get_crypto_type(market_question):
                q = market_question.lower()
                if any(term in q for term in ["eth", "ethereum"]):
                    return "eth"
                elif any(term in q for term in ["sol", "solana"]):
                    return "sol"
                elif any(term in q for term in ["xrp", "ripple"]):
                    return "xrp"
                else:
                    return "btc"  # Default to BTC

            # Get signals for each crypto type found in markets
            crypto_types = set()
            for market in five_min_markets:
                crypto_types.add(get_crypto_type(market.get("question", "")))

            # Collect signals for all crypto types
            all_price_signals = {}
            all_sent_signals = {}

            for crypto in crypto_types:
                update_heartbeat()
                price_signals = price_feed.get_signals(crypto)
                sent_signals = sentiment_feed.get_signals(crypto)
                all_price_signals[crypto] = price_signals
                all_sent_signals[crypto] = sent_signals
                update_heartbeat()

            # compute regime (Hurst exponent) once per crypto type and treat as a
            # "market climate" that is passed to every bot
            regime_map = {}
            import pandas as _pd

            for crypto, p_signals in all_price_signals.items():
                closes = p_signals.get("prices", []) or []
                if closes:
                    regime_map[crypto] = hurst_exponent(_pd.Series(closes))
                else:
                    regime_map[crypto] = 0.5  # neutral/default

            new_trades = 0
            skip_count = 0
            decide_count = 0
            skip_reasons = {}
            now_ts = time.time()
            if skip_cache:
                skip_cache = {
                    k: v for k, v in skip_cache.items() if (now_ts - v) < skip_retry
                }
            for market in five_min_markets:
                market_id = market.get("id") or market.get("market_id")
                of_signals = orderflow_feed.get_signals(market_id, api_key)

                # Get crypto type for this market and use appropriate signals
                crypto_type = get_crypto_type(market.get("question", ""))
                price_signals = all_price_signals.get(crypto_type, {})
                sent_signals = all_sent_signals.get(crypto_type, {})
                combined_signals = {**price_signals, **sent_signals, **of_signals}
                # attach regime signal for bots that care
                regime_val = regime_map.get(crypto_type, 0.5)
                combined_signals["market_regime"] = regime_val

                # Each bot trades independently on its own account
                for bot in bots:
                    key = (bot.name, market_id)
                    if key in executed:
                        continue
                    last_skip = skip_cache.get(key)
                    if last_skip and (now_ts - last_skip) < skip_retry:
                        continue
                        
                    # --- FILTRO DE EXCLUSIVIDADE E CONCENTRAÇÃO DE MERCADO ---
                    # 1. Exclusividade: O mesmo bot não pode ter duas posições no mesmo mercado
                    bot_already_in_market = any(
                        pos.market_id == market_id and pos.bot_name == bot.name
                        for pos in risk_manager.open_positions.values()
                    )
                    if bot_already_in_market:
                        skip_cache[key] = now_ts
                        logger.info(f"[{bot.name}] [SKIP] Bot already has a position in this market.")
                        continue
                    
                    # 2. Concentração: Limite de bots diferentes no mesmo mercado
                    bots_in_market = sum(
                        1 for pos in risk_manager.open_positions.values()
                        if pos.market_id == market_id
                    )
                    max_allowed = getattr(config, "MAX_BOTS_PER_MARKET", 2)
                    if bots_in_market >= max_allowed:
                        skip_cache[key] = now_ts
                        logger.info(f"[{bot.name}] [SKIP] Market reached max concentration ({bots_in_market}/{max_allowed} bots).")
                        skip_count += 1
                        r = f"[SKIP] Market reached max concentration ({bots_in_market}/{max_allowed} bots)."
                        skip_reasons[r] = skip_reasons.get(r, 0) + 1
                        continue
                    # ---------------------------------------------------------

                    try:
                        signal = bot.make_decision(market, combined_signals)
                        decide_count += 1

                        # Skip if bot sees no edge
                        if signal.get("action") == "skip":
                            skip_count += 1
                            skip_cache[key] = now_ts
                            r = (signal.get("reasoning") or "")[:180]
                            if r:
                                skip_reasons[r] = skip_reasons.get(r, 0) + 1
                            continue

                        result = bot.execute(signal, market)
                        if result.get("success"):
                            executed.add(key)
                            new_trades += 1
                            amt = float(signal.get("suggested_amount") or 0.0)
                            amt_s = f"{amt:.4f}" if amt < 0.01 else f"{amt:.2f}"
                            logger.info(
                                f"[{bot.name}] {signal['side'].upper()} ${amt_s} (conf={signal['confidence']:.2f}) on {market.get('question', '')[:50]}"
                            )
                        else:
                            skip_cache[key] = now_ts
                            logger.debug(
                                f"[{bot.name}] Trade failed on {market_id}: {result.get('reason')}"
                            )
                    except Exception as e:
                        logger.error(f"[{bot.name}] Error on {market_id}: {e}")
                        skip_cache[key] = now_ts

            if new_trades > 0:
                logger.info(f"Placed {new_trades} new trades this cycle")
            else:
                top = sorted(skip_reasons.items(), key=lambda x: x[1], reverse=True)[:2]
                if top:
                    logger.info(
                        f"No trades placed this cycle (decisions={decide_count}, skips={skip_count}, top_skip='{top[0][0]}')"
                    )
                else:
                    logger.info(
                        f"No trades placed this cycle (decisions={decide_count}, skips={skip_count})"
                    )

            update_heartbeat()
            time.sleep(TRADE_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Arena stopped by user")
            break
        except Exception as e:
            logger.error(f"Arena loop error: {e}")
            time.sleep(10)


def main():
    parser = argparse.ArgumentParser(description="Polymarket Bot Arena")
    parser.add_argument(
        "--mode",
        choices=["paper", "live"],
        default=None,
        help="Trading mode (default: from config)",
    )
    parser.add_argument(
        "--setup", action="store_true", help="Run setup verification first"
    )
    args = parser.parse_args()

    if args.mode:
        if args.mode == "live":
            confirm = input(
                "You are switching to LIVE trading with real USDC. Type YES to confirm: "
            )
            if confirm.strip() != "YES":
                print("Cancelled. Staying in paper mode.")
                sys.exit(0)
        config.set_trading_mode(args.mode)
        logger.info(f"Trading mode set to: {args.mode}")

    if args.setup:
        import setup

        if not setup.main():
            sys.exit(1)

    # Start Telegram bot in a separate thread
    telegram_thread = threading.Thread(target=telegram_bot.main, daemon=True)
    telegram_thread.start()
    logger.info("Telegram bot started in a background thread.")

    api_key = load_api_key()
    if not api_key:
        print("No Simmer API key found. Run: python3 setup.py")
        sys.exit(1)

    bots = create_default_bots()

    # Save initial bot configs (only if not already saved)
    # A persistência agora é tratada dentro de main_loop() para ser mais robusta
    # e garantir que bots criados em memória sejam salvos antes de rodar
    # existing = {b["bot_name"] for b in db.get_active_bots()}
    # for bot in bots:
    #     if bot.name not in existing:
    #         db.save_bot_config(bot.name, bot.strategy_type, bot.generation, bot.strategy_params)

    # Backfill learning data from old resolved trades that had no trade_features
    backfilled = learning.backfill_from_resolved_trades()
    if backfilled:
        logger.info(f"Backfilled learning from {backfilled} historical trades")

    main_loop(bots, api_key)


if __name__ == "__main__":
    main()
