import db
import sqlite3

def force_expire():
    print("Forcing expiration of stale trades (>2h)...")
    try:
        with db.get_conn() as conn:
            # Selecionar para mostrar antes de deletar
            stale = conn.execute('''
                SELECT id, bot_name, market_question, created_at 
                FROM trades 
                WHERE outcome IS NULL AND created_at < datetime('now', '-1 hour')
            ''').fetchall()
            
            if stale:
                print(f"Found {len(stale)} stale trades:")
                for s in stale:
                    print(f"- [{s['id']}] {s['bot_name']}: {s['market_question']} ({s['created_at']})")
                
                # Executar update
                count = conn.execute('''
                    UPDATE trades SET outcome = 'expired', pnl = 0, resolved_at = datetime('now')
                    WHERE outcome IS NULL AND created_at < datetime('now', '-1 hour')
                ''').rowcount
                print(f"✅ Successfully expired {count} trades.")
            else:
                print("No stale trades found older than 1 hour.")
                
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    force_expire()
