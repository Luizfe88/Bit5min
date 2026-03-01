import os
import sys
from pathlib import Path
import sqlite3

# Adiciona o diretório raiz ao path para pegar as bibliotecas
sys.path.insert(0, str(Path(__file__).parent.resolve()))

import config
import db
import datetime

def reset_bot_limit(bot_name: str, mode: str = "paper"):
    """Reseta o limitador de daily_loss e remove o pausamento para voltar a operar hoje."""
    db_path = config.DB_PATH
    
    print(f"Reseting daily limit for bot: {bot_name} at database: {db_path}...")
    
    # Injetar uma entrada na tabela de arena_state para reset_daily_loss neste minuto.
    now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    db.set_arena_state(f"daily_loss_reset_at:{mode}", now_str)
    
    # 2. Despausar o bot diretamente inserindo: `unpause:bot_name:mode = 1`
    db.set_arena_state(f"unpause:{bot_name}:{mode}", "1")
    
    print(f"Reset successfully completed for {bot_name}. The bot should now be eligible to trade again.")

if __name__ == "__main__":
    bot = "mean_reversion-g4-179"
    reset_bot_limit(bot, mode="paper")
