import json
import sqlite3
conn = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')
cur = conn.execute("SELECT task_id, payload_json FROM tasks WHERE task_id = ?", ('edit-local-7cdd93a849',))
row = cur.fetchone()
if row is None:
    print('not found')
else:
    payload = json.loads(row[1] or '{}')
    print('keys:', list(payload.keys())[:20])
    print('status:', payload.get('status'))
    print('result_path:', payload.get('result_path'))
    outputs = payload.get('outputs') or {}
    print('outputs.workflow:', outputs.get('workflow'))
    print('outputs.style_version:', outputs.get('style_version'))
    qr = json.loads(outputs.get('quality_report') or '{}')
    print('quality_report keys:', list(qr.keys())[:20])
    print('quality_report.passed:', qr.get('passed'))
