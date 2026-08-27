import json
import sqlite3
ETIDS = ['edit-local-b0187c6d33']
conn = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')
for etid in ETIDS:
    cur = conn.execute("SELECT payload_json FROM tasks WHERE task_id = ?", (etid,))
    payload = json.loads(cur.fetchone()[1] or '{}')
    outputs = payload.get('outputs') or {}
    qr_str = outputs.get('quality_report')
    qr = json.loads(qr_str)
    print('all keys:')
    for k in sorted(qr.keys()):
        v = qr[k]
        if isinstance(v, dict):
            print(f'  {k}: <dict>')
        else:
            print(f'  {k}: {v}')
conn.close()
