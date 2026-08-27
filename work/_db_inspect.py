import sqlite3
conn = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')
cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
print('tables:')
for r in cur.fetchall():
    print(' ', r[0])
