import json
import sqlite3
ETIDS = ['edit-local-5d9f8bb3e0', 'edit-local-4aabef3369']
BATCH_MAP = {
    'edit-local-5d9f8bb3e0': 'edit-batch-2e9dc62a6c73',
    'edit-local-4aabef3369': 'edit-batch-stranger_education',
}
conn = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')
for etid in ETIDS:
    bid = BATCH_MAP[etid]
    cur = conn.execute('SELECT payload_json FROM video_editor_batches WHERE batch_id = ?', (bid,))
    bp = json.loads(cur.fetchone()[0])
    item = next(it for it in bp['items'] if it.get('edit_task_id') == etid)
    ep = item.get('edit_plan') or {}
    dp = ep.get('director_plan') or {}
    print('=== ' + etid + ' ===')
    print('  edit_plan keys: ' + str(list(ep.keys())[:10]))
    has_vwp = 'visual_window_plan' in dp
    print('  director_plan has visual_window_plan: ' + str(has_vwp))
    if has_vwp:
        vwp = dp['visual_window_plan']
        print('    target_min: ' + str(vwp.get('target_real_coverage_seconds_min')))
        print('    required_window_count: ' + str(vwp.get('required_window_count')))
        print('    plan_version: ' + str(dp.get('plan_version')))
    else:
        print('  director_plan keys: ' + str(list(dp.keys())[:10]))
        print('  plan_version: ' + str(dp.get('plan_version')))
