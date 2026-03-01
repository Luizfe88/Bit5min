import sqlite3
import pandas as pd
import os
import requests
import json
import logging
from typing import Dict

import config

# Configuração simples de logging para evitar poluição mas permitir erros
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

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

def get_simmer_prices() -> Dict[str, float]:
    """Busca preços atuais de todos os mercados ativos no Simmer."""
    try:
        # Tentar carregar a chave da API do Simmer do config ou arquivo
        api_key = None
        if hasattr(config, "SIMMER_API_KEY_PATH") and os.path.exists(config.SIMMER_API_KEY_PATH):
            with open(config.SIMMER_API_KEY_PATH) as f:
                api_key = json.load(f).get("api_key")
        
        if not api_key:
            return {}

        headers = {"Authorization": f"Bearer {api_key}"}
        resp = requests.get(
            f"{config.SIMMER_BASE_URL}/api/sdk/markets",
            headers=headers,
            params={"status": "active", "limit": 1000},
            timeout=10
        )
        
        if resp.status_code != 200:
            return {}

        markets = resp.json()
        if isinstance(markets, dict):
            markets = markets.get("markets", [])
            
        prices = {}
        for m in markets:
            mid = m.get("id") or m.get("market_id")
            price = m.get("current_price")
            if mid and price is not None:
                prices[mid] = float(price)
        return prices
    except Exception as e:
        print(f"Erro ao buscar preços do Simmer: {e}")
        return {}

def format_pnl_pct(val):
    color = Colors.GREEN if val >= 0 else Colors.RED
    return f"{color}{val:+.2f}%{Colors.RESET}"

def format_pnl_usd(val):
    color = Colors.GREEN if val >= 0 else Colors.RED
    sign = "+" if val >= 0 else "-"
    return f"{color}{sign}${abs(val):.2f}{Colors.RESET}"

def ver_abertas():
    DB_PATH = str(config.DB_PATH)
    if not os.path.exists(DB_PATH):
        print("Banco de dados não encontrado.")
        return

    # 1. Buscar preços atuais
    print(f"{Colors.YELLOW}Buscando preços atuais no Simmer...{Colors.RESET}")
    current_prices = get_simmer_prices()

    conn = sqlite3.connect(DB_PATH)
    # Precisamos de market_id e shares_bought para o cálculo
    query = """
    SELECT id, bot_name, side, amount, shares_bought, market_id, market_question, 
           current_sl, current_tp, tp_triggered, created_at
    FROM trades
    WHERE outcome IS NULL
    ORDER BY created_at DESC
    """
    
    try:
        df = pd.read_sql_query(query, conn)
        if df.empty:
            print("Nenhuma trade aberta no momento.")
            return

        # 2. Calcular Métricas de Lucratividade
        pnl_pcts = []
        pnl_usds = []
        ativos = []
        trailing_status = []

        for _, row in df.iterrows():
            mid = row['market_id']
            side = row['side'].lower()
            amt = float(row['amount'])
            shares = float(row['shares_bought'] or 0)
            
            # Status do Trailing (NOVO)
            if bool(row.get('tp_triggered', 0)):
                trailing_status.append(f"{Colors.MAGENTA}ON{Colors.RESET}")
            else:
                trailing_status.append("OFF")

            if mid in current_prices and shares > 0:
                # Lógica solicitada pelo usuário:
                # Se YES: Custo = Entry_Price, Atual = Current_Price
                # Se NO: Custo = 1 - Entry_Price, Atual = 1 - Current_Price
                # Como Entry_Price (cost per share) no DB já é normalized:
                # cost_per_share = amt / shares
                
                entry_cost_per_token = amt / shares
                current_yes = current_prices[mid]
                
                # Preço atual do token que possuímos (YES ou NO)
                current_token_price = current_yes if side == 'yes' else (1.0 - current_yes)
                
                # PnL % = ((Valor_Atual - Custo_Entrada) / Custo_Entrada) * 100
                p_pct = ((current_token_price - entry_cost_per_token) / entry_cost_per_token) * 100
                # PnL USD = (PnL % / 100) * Size_USD
                p_usd = (p_pct / 100) * amt
                
                pnl_pcts.append(p_pct)
                pnl_usds.append(p_usd)
                ativos.append(f"{current_token_price:.4f}")
            else:
                pnl_pcts.append(0.0)
                pnl_usds.append(0.0)
                if shares <= 0:
                    ativos.append("0-SHR")
                else:
                    ativos.append("MISS")

        df['Preço_At'] = ativos
        df['Lucro %'] = [format_pnl_pct(p) for p in pnl_pcts]
        df['Lucro $'] = [format_pnl_usd(p) for p in pnl_usds]
        df['SL'] = df['current_sl'].apply(lambda x: f"{x:7.4f}" if x else "   —   ")
        df['TP'] = df['current_tp'].apply(lambda x: f"{x:7.4f}" if x else "   —   ")
        df['Trig'] = trailing_status

        # Selecionar colunas legíveis para o humano
        cols_to_show = ['id', 'bot_name', 'side', 'amount', 'Preço_At', 'Lucro %', 'Lucro $', 'SL', 'TP', 'Trig', 'market_question']
        rename_map = {
            'id': 'ID',
            'bot_name': 'Bot',
            'side': 'Lado',
            'amount': 'Tamanho ($)',
            'market_question': 'Mercado'
        }
        
        print("\n" + "="*80)
        print(f" {Colors.BOLD}{Colors.CYAN}POSIÇÕES ABERTAS (Arena v2.0){Colors.RESET}")
        print("="*80)
        
        display_df = df[cols_to_show].rename(columns=rename_map)
        print(display_df.to_string(index=False))
        print("="*80 + "\n")

    except Exception as e:
        print(f"Erro ao processar dados: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

if __name__ == "__main__":
    ver_abertas()