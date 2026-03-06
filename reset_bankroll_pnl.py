import sqlite3

try:
    c = sqlite3.connect('bot_arena.db')
    c.execute("UPDATE trades SET pnl=0 WHERE mode='paper'")
    c.commit()
    print("Bankroll reset!")
except Exception as e:
    print(f"Error: {e}")
finally:
    c.close()

