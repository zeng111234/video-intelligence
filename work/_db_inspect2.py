import sqlite3
conn = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')
cur = conn.execute("SELECT batch_id, status, created_at, updated_at, selected_title FROM video_editor_batches ORDER BY updated_at DESC LIMIT 5")
print('recent batches:')
for r in cur.fetchall():
    print(' ', r)
