import json
import sqlite3
conn = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')
# 把 r8 4b49e4d294 的 task 状态改 failed，让其重跑
for etid in ['edit-local-7cdd93a849', 'edit-local-b0187c6d33', 'edit-local-3bd5724a95', 'edit-local-9bde277eef']:
    cur = conn.execute("SELECT task_id, payload_json FROM tasks WHERE task_id = ?", (etid,))
    row = cur.fetchone()
    if row is None:
        print(f'{etid}: not found, skip')
        continue
    payload = json.loads(row[1] or '{}')
    payload['status'] = 'failed'
    payload['error_message'] = 'force re-run with P0 v3 code'
    cur2 = conn.execute("UPDATE tasks SET payload_json = ? WHERE task_id = ?", (json.dumps(payload), etid))
    print(f'{etid}: status -> failed, rowcount={cur2.rowcount}')
conn.commit()
conn.close()
