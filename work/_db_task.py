import json
import sqlite3
conn = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')
cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'")
print('tasks table:', cur.fetchone())
cur = conn.execute("PRAGMA table_info(tasks)")
print('columns:')
for r in cur.fetchall():
    print(' ', r[1])
cur = conn.execute("SELECT task_id, status, outputs, result_path FROM tasks WHERE task_id = ?", ('edit-local-7cdd93a849',))
row = cur.fetchone()
if row is None:
    print('not found')
else:
    print('task_id:', row[0])
    print('status:', row[1])
    print('result_path:', row[3])
    outputs = json.loads(row[2] or '{}')
    print('workflow:', outputs.get('workflow'))
    print('style_version:', outputs.get('style_version'))
    print('quality_report keys:', list((json.loads(outputs.get('quality_report') or '{}')).keys())[:10])
