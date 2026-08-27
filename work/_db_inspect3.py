import sqlite3
conn = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')
cur = conn.execute("PRAGMA table_info(video_editor_batches)")
print('video_editor_batches columns:')
for r in cur.fetchall():
    print(' ', r)
cur = conn.execute("SELECT batch_id FROM video_editor_batches ORDER BY rowid DESC LIMIT 5")
print('\nrecent batch_ids:')
for r in cur.fetchall():
    print(' ', r[0])
