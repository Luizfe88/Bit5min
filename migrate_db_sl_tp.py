import sqlite3
import config

DB_PATH = config.DB_PATH

def migrate():
    print(f"Migrating database at {DB_PATH}...")
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    columns_to_add = [
        ("sl_price", "REAL"),
        ("tp_price", "REAL"),
        ("current_sl", "REAL"),
        ("current_tp", "REAL")
    ]
    
    for col_name, col_type in columns_to_add:
        try:
            cursor.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")
            print(f"Added column {col_name}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                print(f"Column {col_name} already exists")
            else:
                print(f"Error adding column {col_name}: {e}")
                
    conn.commit()
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
