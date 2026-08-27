import json
import sqlite3
conn = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')
cur = conn.execute("SELECT task_id, payload_json FROM tasks WHERE task_id = ?", ('edit-local-7cdd93a849',))
row = cur.fetchone()
payload = json.loads(row[1] or '{}')
# 把 status 改成 failed 强制走新 pipeline
payload['status'] = 'failed'
payload['error_message'] = 'forced rebuild for p0 acceptance'
cur2 = conn.execute("UPDATE tasks SET payload_json = ? WHERE task_id = ?", (json.dumps(payload), 'edit-local-7cdd93a849'))
conn.commit()
print('updated, rowcount:', cur2.rowcount)
cur3 = conn.execute("SELECT json_extract(payload_json, '$.status') FROM tasks WHERE task_id = ?", ('edit-local-7cdd93a849',))
print('new status:', cur3.fetchone()[0])
