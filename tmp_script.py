import sqlite3, json

conn = sqlite3.connect('bot_arena.db')
conn.row_factory = sqlite3.Row
r = conn.execute('SELECT params FROM bot_configs WHERE bot_name="mean_reversion-g12-449" ORDER BY created_at DESC LIMIT 1').fetchone()
d = dict(r) if r else {}
params = d.get('params', '{}')
try:
    parsed = json.loads(params)
    print('PARAMS:', json.dumps(parsed, indent=2))
except Exception as e:
    print('Error parsing params:', e, params)
