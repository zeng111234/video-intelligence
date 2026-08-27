import json
import sqlite3
ETIDS = ['edit-local-5cbbe581ce']
BATCH_MAP = {'edit-local-5cbbe581ce': 'edit-batch-2e9dc62a6c73'}
conn = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')
for etid in ETIDS:
    cur = conn.execute('SELECT task_id, payload_json FROM tasks WHERE task_id = ?', (etid,))
    payload = json.loads(cur.fetchone()[1])
    psl = json.loads(payload.get('outputs', {}).get('quality_report', ''))
    bid = BATCH_MAP[etid]
    cur2 = conn.execute('SELECT payload_json FROM video_editor_batches WHERE batch_id = ?', (bid,))
    bp = json.loads(cur2.fetchone()[0])
    item = next(it for it in bp['items'] if it.get('edit_task_id') == etid)
    pp = item.get('provider_payload') or {}
    le = pp.get('local_export') or {}
    inner_qr = le.get('quality_report') or {}
    psl_list = inner_qr.get('provider_search_log', [])
    print('=== psl debug fields ===')
    for i, e in enumerate(psl_list):
        dur = e.get('p0v3_matched_duration_seconds')
        acc = e.get('p0v3_accumulated_seconds')
        ff = e.get('force_fresh_reason')
        if dur is not None:
            print('  [' + str(i) + '] dur=' + str(dur) + ' acc=' + str(acc) + ' force_fresh=' + str(ff))
conn.close()
