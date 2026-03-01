import sqlite3
import os
import sys
from pathlib import Path

# Adiciona o diretório raiz ao path para pegar as bibliotecas
sys.path.insert(0, str(Path(__file__).parent.resolve()))

import config

def check_sl_tp_inheritance():
    print("======================================================================")
    print("  VERIFICANDO HERANÇA DE SL/TP DOS BOTS ATIVOS (arena_state/trades)")
    print("======================================================================")
    
    db_path = config.DB_PATH
    if not os.path.exists(db_path):
        print(f"Banco de dados não encontrado em {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Vamos buscar todos os bots que abriram trades nas últimas 48h ou que estão na bot_configs
    cursor.execute("""
        SELECT DISTINCT bot_name 
        FROM trades 
        WHERE created_at >= datetime('now', '-2 days')
        UNION
        SELECT DISTINCT bot_name 
        FROM bot_configs
    """)
    
    bots = cursor.fetchall()
    conn.close()

    if not bots:
        print("Nenhum bot encontrado para verificar.")
        return

    print(f"{'NOME DO BOT':<30} | {'ENABLE_SL_TP_PER_BOT':<20} | {'STATUS'}")
    print("-" * 70)

    for row in bots:
        bot_name = row['bot_name']
        
        # Simula a lógica do BaseBot.__init__
        is_enabled = False
        match_key = None
        
        for key, enabled in config.ENABLE_SL_TP_PER_BOT.items():
            if key in bot_name:
                is_enabled = enabled
                match_key = key
                break
        
        status_str = "[OK] ATIVADO" if is_enabled else "[ERRO] DESATIVADO"
        match_str = f"Match: '{match_key}'" if match_key else "Sem Match"
        
        print(f"{bot_name:<30} | {str(is_enabled):<20} | {status_str} ({match_str})")

if __name__ == "__main__":
    check_sl_tp_inheritance()
