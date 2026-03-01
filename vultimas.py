import sqlite3
import pandas as pd
import os
import config

# Colors for terminal
class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    BLUE = "\033[94m"
    RESET = "\033[0m"
    BOLD = "\033[1m"

def format_pnl_pct(val):
    if val is None: return "—"
    color = Colors.GREEN if val >= 0 else Colors.RED
    return f"{color}{val:+.2f}%{Colors.RESET}"

def format_pnl_usd(val):
    if val is None: return "—"
    color = Colors.GREEN if val >= 0 else Colors.RED
    sign = "+" if val >= 0 else "-"
    return f"{color}{sign}${abs(val):.2f}{Colors.RESET}"

def ver_ultimas(limit=20):
    DB_PATH = str(config.DB_PATH)
    if not os.path.exists(DB_PATH):
        print("Banco de dados não encontrado.")
        return

    conn = sqlite3.connect(DB_PATH)
    
    # Query for resolved trades (finished)
    query = f"""
    SELECT id, bot_name, side, amount, pnl, market_question, outcome, resolved_at
    FROM trades
    WHERE outcome IS NOT NULL
    ORDER BY resolved_at DESC
    LIMIT {limit}
    """
    
    try:
        df = pd.read_sql_query(query, conn)
        if df.empty:
            print(f"{Colors.YELLOW}Nenhuma trade finalizada encontrada.{Colors.RESET}")
            return

        # Calculate PnL %
        pnl_pcts = []
        for _, row in df.iterrows():
            pnl = row['pnl']
            amt = row['amount']
            if pnl is not None and amt > 0:
                pnl_pcts.append((pnl / amt) * 100)
            else:
                pnl_pcts.append(0.0)

        df['Lucro %'] = [format_pnl_pct(p) for p in pnl_pcts]
        df['Lucro $'] = [format_pnl_usd(p) for p in df['pnl']]
        
        # Format resolved_at
        df['Fim'] = df['resolved_at'].apply(lambda x: x.split(' ')[1] if x else '—')

        cols_to_show = ['id', 'bot_name', 'side', 'amount', 'Lucro %', 'Lucro $', 'outcome', 'Fim', 'market_question']
        rename_map = {
            'id': 'ID',
            'bot_name': 'Bot',
            'side': 'Lado',
            'amount': 'Tamanho ($)',
            'outcome': 'Res',
            'market_question': 'Mercado'
        }
        
        print("\n" + "="*90)
        print(f" {Colors.BOLD}{Colors.CYAN}ÚLTIMAS {limit} TRADES FINALIZADAS{Colors.RESET}")
        print("="*90)
        
        display_df = df[cols_to_show].rename(columns=rename_map)
        print(display_df.to_string(index=False))
        print("="*90 + "\n")

    except Exception as e:
        print(f"Erro ao processar dados: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    import sys
    limit = 20
    if len(sys.argv) > 1:
        try:
            limit = int(sys.argv[1])
        except:
            pass
    ver_ultimas(limit)
