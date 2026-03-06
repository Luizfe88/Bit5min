"""SQLite database for all trades, bot performance, evolution history."""

import sqlite3
import json
from pathlib import Path
from datetime import datetime, timedelta
from contextlib import contextmanager
import config

DB_PATH = config.DB_PATH


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_name TEXT NOT NULL,
                market_id TEXT NOT NULL,
                market_question TEXT,
                side TEXT NOT NULL,
                amount REAL NOT NULL,
                confidence REAL,
                reasoning TEXT,
                trade_features TEXT,
                owner_tag TEXT,
                venue TEXT NOT NULL,
                mode TEXT NOT NULL,
                trade_id TEXT,
                shares_bought REAL,
                outcome TEXT,
                pnl REAL,
                resolved_at TEXT,
                sl_price REAL,
                tp_price REAL,
                current_sl REAL,
                current_tp REAL,
                tp_triggered INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS bot_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_name TEXT NOT NULL,
                strategy_type TEXT NOT NULL,
                generation INTEGER DEFAULT 0,
                lineage TEXT,
                params TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                retired_at TEXT
            );

            CREATE TABLE IF NOT EXISTS evolution_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_number INTEGER NOT NULL,
                survivors TEXT NOT NULL,
                replaced TEXT NOT NULL,
                new_bots TEXT NOT NULL,
                rankings TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS daily_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_name TEXT NOT NULL,
                date TEXT NOT NULL,
                trades_count INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                mode TEXT NOT NULL,
                UNIQUE(bot_name, date, mode)
            );

            CREATE TABLE IF NOT EXISTS bot_learning (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_name TEXT NOT NULL,
                feature_key TEXT NOT NULL,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(bot_name, feature_key)
            );

            CREATE TABLE IF NOT EXISTS bot_models (
                bot_name TEXT PRIMARY KEY,
                bias REAL NOT NULL,
                weights TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS arena_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS copytrading_wallets (
                address TEXT PRIMARY KEY,
                label TEXT,
                tracked_since TEXT DEFAULT (datetime('now')),
                total_trades INTEGER DEFAULT 0,
                win_rate REAL,
                total_pnl REAL DEFAULT 0,
                active INTEGER DEFAULT 1,
                profile_type TEXT DEFAULT 'standard',
                ignore_dust_under REAL DEFAULT 50.0,
                max_slippage_offset REAL DEFAULT 0.02,
                timeout_seconds INTEGER DEFAULT 120
            );

            CREATE TABLE IF NOT EXISTS copytrading_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT NOT NULL,
                market_id TEXT,
                side TEXT,
                amount REAL,
                our_trade_id TEXT,
                outcome TEXT,
                pnl REAL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            
            CREATE TABLE IF NOT EXISTS generation_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generation INTEGER NOT NULL,
                bot_name TEXT NOT NULL,
                strategy_type TEXT NOT NULL,
                win_rate REAL,
                total_pnl REAL,
                trades INTEGER,
                params TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)


@contextmanager
def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def log_trade(
    bot_name,
    market_id,
    side,
    amount,
    venue,
    mode,
    confidence=None,
    reasoning=None,
    market_question=None,
    trade_id=None,
    shares_bought=None,
    trade_features=None,
    sl_price=None,
    tp_price=None,
    owner_tag=None,
):
    with get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO trades (bot_name, market_id, market_question, side, amount,
               confidence, reasoning, trade_features, venue, mode, trade_id, shares_bought,
               sl_price, tp_price, current_sl, current_tp, owner_tag)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                bot_name,
                market_id,
                market_question,
                side,
                amount,
                confidence,
                reasoning,
                json.dumps(trade_features) if trade_features else None,
                venue,
                mode,
                trade_id,
                shares_bought,
                sl_price,
                tp_price,
                sl_price,
                tp_price,
                owner_tag,
            ),
        )
        return cursor.lastrowid


def update_position_sl_tp(
    trade_id,
    sl_price=None,
    tp_price=None,
    current_sl=None,
    current_tp=None,
    tp_triggered=None,
):
    """Updates SL/TP values and TP triggered flag for an existing trade."""
    updates = []
    params = []

    if sl_price is not None:
        updates.append("sl_price=?")
        params.append(sl_price)
    if tp_price is not None:
        updates.append("tp_price=?")
        params.append(tp_price)
    if current_sl is not None:
        updates.append("current_sl=?")
        params.append(current_sl)
    if current_tp is not None:
        updates.append("current_tp=?")
        params.append(current_tp)
    if tp_triggered is not None:
        updates.append("tp_triggered=?")
        params.append(1 if tp_triggered else 0)

    if not updates:
        return

    params.append(trade_id)
    query = f"UPDATE trades SET {', '.join(updates)} WHERE id=?"

    with get_conn() as conn:
        conn.execute(query, tuple(params))


def resolve_trade(internal_id, outcome, pnl):
    """Resolve a trade by marking it with outcome and PnL.

    Uses explicit transaction management to ensure persistence.
    """
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.execute(
            "UPDATE trades SET outcome=?, pnl=?, resolved_at=datetime('now') WHERE id=?",
            (outcome, pnl, internal_id),
        )
        # Verificar se a atualização realmente bateu alguma linha
        if cursor.rowcount == 0:
            import logging

            logging.error(
                f"resolve_trade: No rows updated for trade ID {internal_id}. This trade may not exist."
            )
        conn.commit()
        conn.close()
    except Exception as e:
        import logging

        logging.error(
            f"CRITICAL in resolve_trade: Failed to resolve trade {internal_id}: {e}"
        )
        try:
            conn.rollback()
            conn.close()
        except:
            pass
        raise


def get_bot_trades(bot_name, hours=None, limit=50):
    with get_conn() as conn:
        if hours:
            cutoff = (datetime.utcnow() - timedelta(hours=hours)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            rows = conn.execute(
                "SELECT * FROM trades WHERE bot_name=? AND created_at>=? ORDER BY created_at DESC LIMIT ?",
                (bot_name, cutoff, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trades WHERE bot_name=? ORDER BY created_at DESC LIMIT ?",
                (bot_name, limit),
            ).fetchall()
        return [dict(r) for r in rows]


def get_bot_performance(bot_name, hours=12):
    with get_conn() as conn:
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        row = conn.execute(
            """
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN outcome IN ('win', 'exit_tp') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN outcome IN ('loss', 'exit_sl') THEN 1 ELSE 0 END) as losses,
                COALESCE(SUM(pnl), 0) as total_pnl,
                COALESCE(AVG(pnl), 0) as avg_pnl
            FROM trades
            WHERE bot_name=? AND created_at>=? AND outcome IN ('win', 'loss', 'exit_tp', 'exit_sl')
        """,
            (bot_name, cutoff),
        ).fetchone()
        result = dict(row)
        result["wins"] = result["wins"] or 0
        result["losses"] = result["losses"] or 0
        total = result["wins"] + result["losses"]
        result["win_rate"] = result["wins"] / total if total > 0 else 0
        return result


def get_all_bots_performance(hours=12):
    with get_conn() as conn:
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        rows = conn.execute(
            """
            SELECT
                bot_name,
                COUNT(*) as total_trades,
                SUM(CASE WHEN outcome IN ('win', 'exit_tp') THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN outcome IN ('loss', 'exit_sl') THEN 1 ELSE 0 END) as losses,
                COALESCE(SUM(pnl), 0) as total_pnl
            FROM trades
            WHERE created_at>=? AND outcome IN ('win', 'loss', 'exit_tp', 'exit_sl')
            GROUP BY bot_name
        """,
            (cutoff,),
        ).fetchall()
        results = {}
        for r in rows:
            d = dict(r)
            d["wins"] = d["wins"] or 0
            d["losses"] = d["losses"] or 0
            total = d["wins"] + d["losses"]
            d["win_rate"] = d["wins"] / total if total > 0 else 0
            results[d["bot_name"]] = d
        return results


def save_generation_snapshot(
    generation, bot_name, strategy_type, win_rate, total_pnl, trades, params
):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO generation_snapshots 
               (generation, bot_name, strategy_type, win_rate, total_pnl, trades, params)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                int(generation),
                bot_name,
                strategy_type,
                float(win_rate),
                float(total_pnl),
                int(trades),
                json.dumps(params),
            ),
        )


def save_bot_config(bot_name, strategy_type, generation, params, lineage=None):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO bot_configs (bot_name, strategy_type, generation, lineage, params)
               VALUES (?, ?, ?, ?, ?)""",
            (bot_name, strategy_type, generation, lineage, json.dumps(params)),
        )


def retire_bot(bot_name):
    with get_conn() as conn:
        conn.execute(
            "UPDATE bot_configs SET active=0, retired_at=datetime('now') WHERE bot_name=? AND active=1",
            (bot_name,),
        )


def get_active_bots():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM bot_configs WHERE active=1 ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]


def log_evolution(cycle_number, survivors, replaced, new_bots, rankings):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO evolution_events (cycle_number, survivors, replaced, new_bots, rankings)
               VALUES (?, ?, ?, ?, ?)""",
            (
                cycle_number,
                json.dumps(survivors),
                json.dumps(replaced),
                json.dumps(new_bots),
                json.dumps(rankings),
            ),
        )


def get_evolution_history(limit=20):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM evolution_events ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_total_daily_loss(mode="paper"):
    with get_conn() as conn:
        # Compute cutoff: start of today OR manual reset time, whichever is later
        now_utc = datetime.utcnow()
        today_start = datetime(now_utc.year, now_utc.month, now_utc.day, 0, 0, 0)
        reset_key = f"daily_loss_reset_at:{mode}"
        reset_at = get_arena_state(reset_key)
        cutoff = today_start
        if reset_at:
            try:
                ra = datetime.strptime(reset_at, "%Y-%m-%d %H:%M:%S")
                if ra > cutoff:
                    cutoff = ra
            except Exception:
                pass
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            """
            SELECT COALESCE(SUM(pnl), 0) as total_loss
            FROM trades
            WHERE mode=? AND created_at>=? AND pnl < 0 AND outcome IS NOT NULL
        """,
            (mode, cutoff_str),
        ).fetchone()
        return abs(dict(row)["total_loss"])


def get_daily_net_pnl(mode="paper"):
    """
    Returns the SUM of all PnL (realized) resolved today (since last reset).
    Positive = profit, Negative = net loss.
    """
    with get_conn() as conn:
        now_utc = datetime.utcnow()
        today_start = datetime(now_utc.year, now_utc.month, now_utc.day, 0, 0, 0)
        reset_key = f"daily_loss_reset_at:{mode}"
        reset_at = get_arena_state(reset_key)
        cutoff = today_start
        if reset_at:
            try:
                ra = datetime.strptime(reset_at, "%Y-%m-%d %H:%M:%S")
                if ra > cutoff:
                    cutoff = ra
            except Exception:
                pass
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            """
            SELECT COALESCE(SUM(pnl), 0) as net_pnl
            FROM trades
            WHERE mode=? AND created_at>=? AND outcome IS NOT NULL
        """,
            (mode, cutoff_str),
        ).fetchone()
        return float(dict(row)["net_pnl"])


def get_bot_daily_loss(bot_name, mode="paper"):
    with get_conn() as conn:
        # Compute cutoff: start of today OR manual reset time, whichever is later
        now_utc = datetime.utcnow()
        today_start = datetime(now_utc.year, now_utc.month, now_utc.day, 0, 0, 0)
        reset_key = f"daily_loss_reset_at:{mode}"
        reset_at = get_arena_state(reset_key)
        cutoff = today_start
        if reset_at:
            try:
                ra = datetime.strptime(reset_at, "%Y-%m-%d %H:%M:%S")
                if ra > cutoff:
                    cutoff = ra
            except Exception:
                pass
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            """
            SELECT COALESCE(SUM(pnl), 0) as total_loss
            FROM trades
            WHERE bot_name=? AND mode=? AND created_at>=? AND pnl < 0 AND outcome IS NOT NULL
        """,
            (bot_name, mode, cutoff_str),
        ).fetchone()
        return abs(dict(row)["total_loss"])


def get_bot_daily_net_pnl(bot_name, mode="paper"):
    """
    Returns the SUM of all PnL (realized) for a specific bot resolved today.
    """
    with get_conn() as conn:
        now_utc = datetime.utcnow()
        today_start = datetime(now_utc.year, now_utc.month, now_utc.day, 0, 0, 0)
        reset_key = f"daily_loss_reset_at:{mode}"
        reset_at = get_arena_state(reset_key)
        cutoff = today_start
        if reset_at:
            try:
                ra = datetime.strptime(reset_at, "%Y-%m-%d %H:%M:%S")
                if ra > cutoff:
                    cutoff = ra
            except Exception:
                pass
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            """
            SELECT COALESCE(SUM(pnl), 0) as net_pnl
            FROM trades
            WHERE bot_name=? AND mode=? AND created_at>=? AND outcome IS NOT NULL
        """,
            (bot_name, mode, cutoff_str),
        ).fetchone()
        return float(dict(row)["net_pnl"])


def get_active_bot_names():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT bot_name FROM bot_configs WHERE active=1"
        ).fetchall()
        return [r["bot_name"] for r in rows]


def reset_arena_day(mode="paper"):
    """
    Manually reset daily limits for the given mode:
    - Sets daily_loss_reset_at:<mode> to now (UTC), so daily loss counters restart
    - Unpauses all active bots for this mode
    """
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    set_arena_state(f"daily_loss_reset_at:{mode}", now_str)
    try:
        bot_names = get_active_bot_names()
        for name in bot_names:
            set_arena_state(f"unpause:{name}:{mode}", "1")
    except Exception:
        pass


def get_dashboard_stats():
    with get_conn() as conn:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()

        # Exclude phantom trades (pnl=0 resolved from voting era)
        today_stats = conn.execute(
            """
            SELECT COUNT(*) as trades, COALESCE(SUM(pnl), 0) as pnl,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl < 0 AND outcome IS NOT NULL THEN 1 ELSE 0 END) as losses
            FROM trades WHERE date(created_at)=?
                AND NOT (outcome IS NOT NULL AND pnl = 0)
        """,
            (today,),
        ).fetchone()

        week_stats = conn.execute(
            """
            SELECT COUNT(*) as trades, COALESCE(SUM(pnl), 0) as pnl,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl < 0 AND outcome IS NOT NULL THEN 1 ELSE 0 END) as losses
            FROM trades WHERE created_at>=?
                AND NOT (outcome IS NOT NULL AND pnl = 0)
        """,
            (week_ago,),
        ).fetchone()

        all_stats = conn.execute("""
            SELECT COUNT(*) as trades, COALESCE(SUM(pnl), 0) as pnl,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl < 0 AND outcome IS NOT NULL THEN 1 ELSE 0 END) as losses
            FROM trades
                WHERE NOT (outcome IS NOT NULL AND pnl = 0)
        """).fetchone()

        return {
            "today": dict(today_stats),
            "week": dict(week_stats),
            "all_time": dict(all_stats),
        }


def get_bot_consecutive_losses(bot_name, mode="paper"):
    """Get number of consecutive losses for a bot"""
    with get_conn() as conn:
        # If we have a last evolution timestamp, only count trades since then
        try:
            last_evo = get_arena_state("last_evolution_time")
        except Exception:
            last_evo = None
        where_extra = ""
        params = [bot_name, mode]
        if last_evo:
            try:
                ts = float(last_evo)
                cutoff = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                where_extra = " AND created_at >= ? "
                params.append(cutoff)
            except Exception:
                pass
        # Get last trades ordered by time (most recent first)
        trades = conn.execute(
            """
            SELECT pnl, outcome 
            FROM trades 
            WHERE bot_name = ? AND mode = ? AND outcome IS NOT NULL
            {} 
            ORDER BY created_at DESC
            LIMIT 10
        """.format(where_extra),
            params,
        ).fetchall()

        consecutive_losses = 0
        for trade in trades:
            if trade["pnl"] < 0:  # Loss
                consecutive_losses += 1
            elif trade["pnl"] > 0:  # Win
                break  # Stop counting at first win
            # Skip trades with pnl = 0 (push/cancel)

        return consecutive_losses


def get_arena_state(key, default=None):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM arena_state WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else default


def set_arena_state(key, value):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO arena_state (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value=?, updated_at=datetime('now')""",
            (key, str(value), str(value)),
        )


def get_total_open_position_value(bot_name, mode="paper"):
    """Get total value of all open positions for a bot"""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0) as total_amount
            FROM trades
            WHERE bot_name=? AND mode=? AND outcome IS NULL
        """,
            (bot_name, mode),
        ).fetchone()
        return dict(row)["total_amount"]


def get_total_open_position_value_all_bots(mode="paper"):
    """Get total value of all open positions for all bots"""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0) as total_amount
            FROM trades
            WHERE mode=? AND outcome IS NULL
        """,
            (mode,),
        ).fetchone()
        return dict(row)["total_amount"]


def get_bot_current_capital(bot_name, mode="paper"):
    """Get current capital for a specific bot (initial + PnL acumulado)"""
    import config
    with get_conn() as conn:
        active_bots = get_active_bot_names()
        num_bots = max(1, len(active_bots))
        
        # Get initial capital distributed evenly
        if mode == "paper":
            initial_capital = config.PAPER_STARTING_BALANCE / num_bots
        else:
            initial_capital = config.LIVE_STARTING_BALANCE / num_bots

        # Get total PnL for this bot
        row = conn.execute(
            """
            SELECT COALESCE(SUM(pnl), 0) as total_pnl
            FROM trades
            WHERE bot_name=? AND mode=? AND outcome IS NOT NULL
        """,
            (bot_name, mode),
        ).fetchone()

        total_pnl = dict(row)["total_pnl"]
        return initial_capital + total_pnl


def get_total_current_capital(mode="paper"):
    """Get total current capital across all bots (Initial + PnL)"""
    import config
    
    # Base capital from config
    initial = config.PAPER_STARTING_BALANCE if mode == "paper" else config.LIVE_STARTING_BALANCE
    
    # If we have a virtual_bankroll in state, we use it as a reference if it was recently set
    # but the primary source of truth for "change" is the trades table PnL.
    try:
        with get_conn() as conn:
            # Pega o PnL total realisado
            row_pnl = conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) as total_pnl FROM trades WHERE mode=? AND outcome IS NOT NULL",
                (mode,)
            ).fetchone()
            total_realized_pnl = float(dict(row_pnl)["total_pnl"])
            
            return float(initial) + total_realized_pnl
    except Exception:
        return float(initial)


# ===== FUNÇÕES PARA SISTEMA DE EVOLUÇÃO POR TRADES =====


def get_evolution_state():
    """Obtém estado do sistema de evolução"""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT value FROM arena_state 
            WHERE key = 'evolution_state'
        """).fetchone()

        if row:
            try:
                return json.loads(dict(row)["value"])
            except (json.JSONDecodeError, KeyError):
                return None
        return None


def save_evolution_state(state_dict):
    """Salva estado do sistema de evolução"""
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO arena_state (key, value) VALUES (?, ?)""",
            ("evolution_state", json.dumps(state_dict)),
        )


def record_resolved_trade(bot_name, trade_result):
    """Registra trade resolvido para contador global"""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO resolved_trades (bot_name, market_id, outcome, pnl, resolved_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                bot_name,
                trade_result.get("market_id"),
                trade_result.get("outcome"),
                trade_result.get("pnl"),
                datetime.now().isoformat(),
            ),
        )


def get_global_resolved_trades_count(hours=None):
    """Obtém contagem global de trades resolvidos"""
    with get_conn() as conn:
        if hours:
            since = (datetime.now() - timedelta(hours=hours)).isoformat()
            row = conn.execute(
                """SELECT COUNT(*) as count FROM resolved_trades 
                   WHERE resolved_at >= ?""",
                (since,),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT COUNT(*) as count FROM resolved_trades"""
            ).fetchone()

        return dict(row)["count"] if row else 0


def log_evolution(
    cycle_number,
    survivor_names,
    replaced_names,
    new_bot_names,
    rankings,
    trigger_reason="manual",
):
    """Registra evento de evolução no banco"""
    with get_conn() as conn:
        try:
            rankings_clean = [
                {k: v for k, v in r.items() if k != "bot"} for r in (rankings or [])
            ]
        except Exception:
            rankings_clean = rankings
        conn.execute(
            """INSERT INTO evolution_events 
               (cycle_number, survivors, replaced, new_bots, rankings, trigger_reason)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                cycle_number,
                json.dumps(survivor_names),
                json.dumps(replaced_names),
                json.dumps(new_bot_names),
                json.dumps(rankings_clean),
                trigger_reason,
            ),
        )


def get_last_evolution_event():
    """Obtém último evento de evolução"""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM evolution_events 
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()

        return dict(row) if row else None


def get_evolution_history(limit=10):
    """Obtém histórico de evoluções"""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM evolution_events 
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()

        return [dict(row) for row in rows]


def get_resolved_trades_stats(hours=24):
    """Obtém estatísticas de trades resolvidos"""
    with get_conn() as conn:
        since = (datetime.now() - timedelta(hours=hours)).isoformat()

        # Total de trades por bot
        bot_stats = conn.execute(
            """SELECT bot_name, COUNT(*) as count, SUM(pnl) as total_pnl,
                      AVG(pnl) as avg_pnl
               FROM resolved_trades 
               WHERE resolved_at >= ?
               GROUP BY bot_name""",
            (since,),
        ).fetchall()

        # Total global
        total = conn.execute(
            """SELECT COUNT(*) as count, SUM(pnl) as total_pnl,
                      AVG(pnl) as avg_pnl
               FROM resolved_trades 
               WHERE resolved_at >= ?""",
            (since,),
        ).fetchone()

        return {
            "by_bot": [dict(row) for row in bot_stats],
            "total": dict(total)
            if total
            else {"count": 0, "total_pnl": 0, "avg_pnl": 0},
        }


# Criar tabela de trades resolvidos se não existir
def _create_resolved_trades_table():
    """Cria tabela auxiliar de trades resolvidos"""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS resolved_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_name TEXT NOT NULL,
                market_id TEXT,
                outcome TEXT,
                pnl REAL,
                resolved_at TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)


def _ensure_evolution_events_schema():
    """Garante que a tabela evolution_events tenha coluna trigger_reason"""
    with get_conn() as conn:
        try:
            cols = conn.execute("PRAGMA table_info(evolution_events)").fetchall()
            names = [dict(c).get("name") for c in cols]
            if "trigger_reason" not in names:
                conn.execute(
                    "ALTER TABLE evolution_events ADD COLUMN trigger_reason TEXT"
                )
        except Exception:
            pass


# Inicialização

def get_copytrading_wallet(address):
    """Retrieve all configurable settings for a tracked wallet."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM copytrading_wallets WHERE address=?",
            (address,)
        ).fetchone()
        return dict(row) if row else None
init_db()
_create_resolved_trades_table()
_ensure_evolution_events_schema()
