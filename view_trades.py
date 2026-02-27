import db
import json
import sqlite3

def view_sl_tp():
    print("Fetching active trades with SL/TP info...")
    try:
        with db.get_conn() as conn:
            # Query com row_factory implicito (sqlite3.Row) ou fetchall retornando tuplas
            cursor = conn.execute('''
                SELECT id, bot_name, market_question, side, amount, trade_features, sl_price, tp_price, current_sl, current_tp, created_at
                FROM trades 
                WHERE outcome IS NULL
                ORDER BY created_at DESC
            ''')
            
            # Obter nomes das colunas para criar dicionário se necessário
            cols = [description[0] for description in cursor.description]
            trades = cursor.fetchall()
            
            if not trades:
                print("No active trades found.")
                return

            # Header
            print(f"{'ID':<5} | {'Bot':<15} | {'Side':<4} | {'Entry':<6} | {'SL':<8} | {'TP':<8} | {'Market'}")
            print("-" * 110)
            
            for row in trades:
                # Converter para dict manualmente para garantir compatibilidade
                t = dict(zip(cols, row))
                
                # Prioriza colunas do DB, fallback para JSON
                sl = t.get('current_sl') or t.get('sl_price')
                tp = t.get('current_tp') or t.get('tp_price')
                
                # Tentar extrair do trade_features se colunas estiverem vazias
                entry_price = 0.5 # Default fallback
                
                if sl is None or tp is None or entry_price == 0.5:
                    try:
                        feats_str = t.get('trade_features')
                        if feats_str:
                            feats = json.loads(feats_str)
                            if sl is None: sl = feats.get('sl_price')
                            if tp is None: tp = feats.get('tp_price')
                            
                            # Tentar pegar entry price
                            p = feats.get('market_price')
                            if p: entry_price = float(p)
                    except:
                        pass
                
                # Formatação
                sl_str = f"{sl:.4f}" if sl else "-"
                tp_str = f"{tp:.4f}" if tp else "-"
                entry_str = f"{entry_price:.4f}"
                
                # Encurtar nome do mercado
                market = (t.get('market_question') or "Unknown").replace("Bitcoin Up or Down - ", "")[:45]
                bot_name = (t.get('bot_name') or "Unknown")[:15]
                side = (t.get('side') or "?")[:4]
                
                print(f"{t['id']:<5} | {bot_name:<15} | {side:<4} | {entry_str:<6} | {sl_str:<8} | {tp_str:<8} | {market}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    view_sl_tp()
