import sqlite3
import pandas as pd
import os

import config

# Caminho do banco (ajuste se necessário)
DB_PATH = str(config.DB_PATH)

def ver_abertas():
    if not os.path.exists(DB_PATH):
        print("Banco de dados não encontrado.")
        return

    conn = sqlite3.connect(DB_PATH)
    query = """
    SELECT bot_name, side, amount, market_question, created_at
    FROM trades
    WHERE outcome IS NULL
    ORDER BY created_at DESC
    """
    
    try:
        df = pd.read_sql_query(query, conn)
        if df.empty:
            print("Nenhuma trade aberta no momento.")
        else:
            print(df.to_string(index=False))
    except Exception as e:
        print(f"Erro ao ler banco: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    ver_abertas()