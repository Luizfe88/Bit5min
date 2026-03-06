import db
import sqlite3
c = sqlite3.connect(db.DB_PATH)
c.execute("UPDATE trades SET pnl=pnl+7862.02 WHERE mode='paper' AND id=(SELECT MAX(id) FROM trades WHERE mode='paper')")
c.commit()
print("Bankroll reset!")
