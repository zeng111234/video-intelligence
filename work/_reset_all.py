import json
import sqlite3
ETIDS = ['edit-local-5d9f8bb3e0', 'edit-local-4aabef3369', 'edit-local-7c69961425', 'edit-local-260167d25a']
conn = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')
for etid in ETIDS:
    cur = conn.execute("SELECT task_id, payload_json FROM tasks WHERE task_id = ?", (etid,))
    row = cur.fetchone()
    if row is None:
        print(f'{etid}: not found, skip')
        continue
    payload = json.loads(row[1] or '{}')
    payload['status'] = 'failed'
    payload['error_message'] = 're-run with fresh backend'
    cur2 = conn.execute("UPDATE tasks SET payload_json = ? WHERE task_id = ?", (json.dumps(payload), etid))
    print(f'{etid}: -> failed, rowcount={cur2.rowcount}')
conn.commit()
conn.close()
