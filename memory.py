import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect("database.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS memory (
    user_id TEXT,
    message TEXT,
    timestamp DATETIME
)
""")
conn.commit()

def save_message(user_id: str, message: str):
    cursor.execute("INSERT INTO memory (user_id, message, timestamp) VALUES (?, ?, ?)",
                   (user_id, message, datetime.now()))
    conn.commit()

def load_user_history(user_id: str):
    cursor.execute("SELECT message FROM memory WHERE user_id=? ORDER BY timestamp DESC LIMIT 20", (user_id,))
    return [row[0] for row in cursor.fetchall()][::-1]

def clean_old_memory(days=30):
    cutoff = datetime.now() - timedelta(days=days)
    cursor.execute("DELETE FROM memory WHERE timestamp < ?", (cutoff,))
    conn.commit()

def backup_memory():
    backup_file = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    with open("database.db", "rb") as src, open(backup_file, "wb") as dst:
        dst.write(src.read())
    return backup_file
  
