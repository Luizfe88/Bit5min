import sqlite3
import json
import os

import config

# Caminho para o seu banco de dados
DB_PATH = str(config.DB_PATH)

def mostrar_evolucao():
    if not os.path.exists(DB_PATH):
        print(f"Erro: Banco de dados não encontrado em {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Pega todos os bots ordenados por geração (do mais novo para o mais velho)
    cursor.execute("""
        SELECT bot_name, strategy_type, generation, params, lineage, datetime(created_at, 'localtime') as created_at 
        FROM bot_configs 
        ORDER BY strategy_type, generation ASC
    """)
    bots = cursor.fetchall()
    conn.close()

    print(f"{'BOT NAME':<25} | {'HORÁRIO':<19} | {'GEN':<3} | {'DNA (PARAMETROS MUDADOS)'}")
    print("-" * 100)

    # Agrupa por estratégia para comparar pai vs filho
    history = {}
    
    for bot in bots:
        strat = bot['strategy_type']
        gen = bot['generation']
        params = json.loads(bot['params'])
        
        if strat not in history:
            history[strat] = []
        
        # Obter o horário, se disponível
        horario = bot['created_at'] if 'created_at' in bot.keys() else 'N/A'
        history[strat].append({'name': bot['bot_name'], 'gen': gen, 'params': params, 'horario': horario})

    # Mostra a comparação
    for strat, generations in history.items():
        if len(generations) > 1:
            print(f"\n--- Evolução da Estratégia: {strat.upper()} ---")
            base_params = generations[0]['params'] # Pega a v1 como base
            
            for i, bot in enumerate(generations):
                diff = ""
                if i > 0: # Se for filho, compara com o pai (anterior)
                    pai_params = generations[i-1]['params']
                    changes = []
                    for key, val in bot['params'].items():
                        if key in pai_params and pai_params[key] != val:
                            # Formata float para ficar limpo
                            val_fmt = f"{val:.4f}" if isinstance(val, float) else val
                            pai_fmt = f"{pai_params[key]:.4f}" if isinstance(pai_params[key], float) else pai_params[key]
                            changes.append(f"{key}: {pai_fmt} -> {val_fmt}")
                    diff = " | ".join(changes)
                
                horario_fmt = bot.get('horario', 'N/A')
                print(f"{bot['name']:<25} | {horario_fmt:<19} | G{bot['gen']:<2} | {diff}")
        else:
            # Robôs que não evoluíram
            pass

if __name__ == "__main__":
    mostrar_evolucao()