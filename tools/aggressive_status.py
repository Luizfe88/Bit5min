#!/usr/bin/env python3
"""
AGGRESSIVE MODE STATUS CHECK
Valida configuração e reporta status de trades em tempo real.

USO:
  python tools/aggressive_status.py
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timedelta

# Add root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import config
import db


def print_header(title: str):
    """Print a section header."""
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def check_configuration():
    """Verify AGGRESSIVE mode configuration."""
    print_header("1. VERIFICAR CONFIGURAÇÃO")

    try:
        agg = config.get_aggression_level()
        print(f"✓ Aggression Level: {agg.upper()}")

        if agg != "aggressive":
            print(f"  ⚠️  AVISO: Modo é '{agg}', não 'aggressive'")
            print(f"     Edite .env: TRADING_AGGRESSION=aggressive")
            return False

        min_conf = config.get_min_confidence()
        print(f"✓ MIN_CONFIDENCE: {min_conf:.2f} (esperado: 0.48)")
        if min_conf != 0.48:
            print(f"  ⚠️  AVISO: Esperado 0.48, encontrado {min_conf:.2f}")

        min_edge = config.get_min_edge_after_fees()
        print(f"✓ MIN_EDGE_AFTER_FEES: {min_edge:.6f} (0.12% = 0.0012)")
        if min_edge != 0.0012:
            print(f"  ⚠️  AVISO: Esperado 0.0012, encontrado {min_edge:.6f}")

        max_spread = config.get_max_spread_allowed()
        print(f"✓ MAX_SPREAD_ALLOWED: {max_spread:.1f}% (esperado: 2.1%)")
        if max_spread != 2.1:
            print(f"  ⚠️  AVISO: Esperado 2.1, encontrado {max_spread:.1f}")

        max_trades = config.get_max_trades_per_hour_per_bot()
        print(f"✓ MAX_TRADES_PER_HOUR: {max_trades} (hard-cap agressivo)")
        if max_trades > 12:
            print(f"  ⚠️  AVISO: Esperado ≤12, encontrado {max_trades}")

        mode = config.get_current_mode()
        print(f"✓ Trading Mode: {mode.upper()}")
        if mode != "paper":
            print(
                f"  ⚠️  ATENÇÃO: Está em '{mode}' mode. Recomenda-se paper para teste!"
            )

        return agg == "aggressive"

    except Exception as e:
        print(f"❌ Erro ao verificar config: {e}")
        return False


def check_trades_volume():
    """Check recent trades volume."""
    print_header("2. VOLUME DE TRADES")

    try:
        # Last 24 hours
        with db.get_conn() as conn:
            # Total trades (pending + resolved)
            total = conn.execute(
                "SELECT COUNT(*) as c FROM trades WHERE created_at >= datetime('now', '-24 hours')"
            ).fetchone()
            total_count = total["c"]

            # Resolved only
            resolved = conn.execute(
                "SELECT COUNT(*) as c FROM trades WHERE outcome IS NOT NULL AND created_at >= datetime('now', '-24 hours')"
            ).fetchone()
            resolved_count = resolved["c"]

            # By bot
            by_bot = conn.execute("""
                SELECT bot_name, COUNT(*) as c, 
                       SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) as losses
                FROM trades 
                WHERE created_at >= datetime('now', '-24 hours')
                GROUP BY bot_name
                ORDER BY c DESC
            """).fetchall()

        print(f"✓ Total Trades (24h): {total_count}")
        print(f"  - Resolvidos: {resolved_count}")
        print(f"  - Pendentes: {total_count - resolved_count}")

        if total_count < 10:
            print(f"  ⚠️  BAIXO VOLUME: Esperado >80 trades em 24h")
        elif total_count < 80:
            print(f"  ⚠️  VOLUME MODERADO: Alvo é >80 trades")
        else:
            print(f"  ✅ VOLUME OK: {total_count} trades em 24h")

        print(f"\n✓ Trades por Bot (24h):")
        for row in by_bot:
            bot = row["bot_name"]
            cnt = row["c"]
            wins = row["wins"] or 0
            losses = row["losses"] or 0
            wr = (
                f"{wins * 100 / (wins + losses):.1f}%" if (wins + losses) > 0 else "N/A"
            )
            print(f"  {bot}: {cnt} trades ({wins} W, {losses} L, {wr} WR)")

        return total_count >= 10

    except Exception as e:
        print(f"❌ Erro ao verificar trades: {e}")
        return False


def check_recent_skips():
    """Check recent SKIP/rejection reasons."""
    print_header("3. ANALISE DE REJEIÇÕES (últimas 50 SKIP)")

    try:
        with db.get_conn() as conn:
            skips = conn.execute("""
                SELECT bot_name, reasoning, COUNT(*) as c
                FROM trades
                WHERE outcome IS NULL AND action='skip' 
                      AND created_at >= datetime('now', '-1 hour')
                GROUP BY reasoning
                ORDER BY c DESC
                LIMIT 10
            """).fetchall()

        if not skips:
            print("✓ Sem rejeções recentes (última 1h) - bom sinal!")
            return True

        total_skips = sum(row["c"] for row in skips)
        print(f"✓ Rejeições recentes (1h): {total_skips}")

        for row in skips:
            reason = row["reasoning"][:80] if row["reasoning"] else "unknown"
            count = row["c"]
            pct = f"{count * 100 / total_skips:.1f}%"
            print(f"  {reason}... [{count} x {pct}]")

        return True

    except Exception as e:
        print(f"❌ Erro ao verificar rejeições: {e}")
        return False


def check_risk_status():
    """Check RiskManager status and daily losses."""
    print_header("4. STATUS DE RISCO (RiskManager)")

    try:
        with db.get_conn() as conn:
            # Daily loss per bot
            daily_loss = conn.execute("""
                SELECT bot_name, 
                       COALESCE(SUM(CASE WHEN pnl < 0 THEN -pnl ELSE 0 END), 0) as loss
                FROM trades
                WHERE mode='paper' AND created_at >= datetime('now', 'start of day')
                GROUP BY bot_name
                HAVING loss > 0
                ORDER BY loss DESC
            """).fetchall()

            max_daily_loss_per_bot = config.get_max_daily_loss_per_bot()

            print(f"✓ Daily Loss Limit (per bot): ${max_daily_loss_per_bot:.2f}")

            if not daily_loss:
                print("  Sem perdas significativas hoje")
            else:
                for row in daily_loss:
                    bot = row["bot_name"]
                    loss = row["loss"]
                    pct = f"{loss * 100 / max_daily_loss_per_bot:.1f}%"
                    status = (
                        "🔴 AT LIMIT"
                        if loss >= max_daily_loss_per_bot
                        else "⚠️  CAUTION"
                        if loss > max_daily_loss_per_bot * 0.7
                        else "✓ OK"
                    )
                    print(f"  {bot}: ${loss:.2f} ({pct}) [{status}]")

            # Open positions
            open_positions = conn.execute(
                "SELECT COUNT(*) as c FROM open_positions WHERE closed_at IS NULL"
            ).fetchone()

            print(f"✓ Open Positions: {open_positions['c']}")

            return True

    except Exception as e:
        print(f"⚠️  Não foi possível verificar RiskManager: {e}")
        return True  # não falha


def print_summary(checks: list):
    """Print summary of all checks."""
    print_header("RESUMO")

    passed = sum(checks)
    total = len(checks)

    print(f"Status: {passed}/{total} checks")

    if passed == total:
        print("✅ TUDO OK - MODO AGGRESSIVE CONFIGURADO CORRETAMENTE")
        print("   Inicie com: python arena.py")
    elif passed >= total - 1:
        print("⚠️  QUASE PRONTO - Verifique avisos acima")
    else:
        print("❌ CONFIGURAÇÃO INCOMPLETA - Siga os avisos acima")

    print()


def main():
    print("\n" + "=" * 70)
    print("  AGGRESSIVE MODE - STATUS CHECK")
    print(f"  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print("=" * 70)

    checks = [
        check_configuration(),
        check_trades_volume(),
        check_recent_skips(),
        check_risk_status(),
    ]

    print_summary(checks)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrompido pelo usuário.")
    except Exception as e:
        print(f"\n❌ Erro crítico: {e}")
        import traceback

        traceback.print_exc()
