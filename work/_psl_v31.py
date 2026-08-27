import json
import sqlite3
ETIDS = ['edit-local-696e0f42c6', 'edit-local-8102cef58a', 'edit-local-d7552cbcdc', 'edit-local-2c72adc6e6']
BATCH_MAP = {
    'edit-local-696e0f42c6': 'edit-batch-2e9dc62a6c73',
    'edit-local-8102cef58a': 'edit-batch-stranger_education',
    'edit-local-d7552cbcdc': 'edit-batch-stranger_saas',
    'edit-local-2c72adc6e6': 'edit-batch-stranger_logistics',
}
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
    print('=== ' + etid + ' ===')
    psl_list = inner_qr.get('provider_search_log', [])
    fresh_count = 0
    for i, e in enumerate(psl_list):
        if e.get('force_fresh_reason'):
            fresh_count += 1
        if e.get('provider_status') in ('ready', 'fallback_ready', 'cache_reused', 'no_results', 'unavailable'):
            continue
    statuses = [e.get('provider_status') for e in psl_list]
    print('  psl statuses: ' + str(set(statuses)))
    print('  psl[0]: attempt=' + str(psl_list[0].get('provider_attempted')) + ' status=' + str(psl_list[0].get('provider_status')) + ' force_fresh_reason=' + str(psl_list[0].get('force_fresh_reason')))
    print('  real_stock_broll: ' + str(psl.get('real_stock_broll_coverage_ratio')))
    print('  candidate_count in psl[0]: ' + str(psl_list[0].get('candidate_count')))
    print('  provider_attempt_details[0]: ' + str(psl_list[0].get('provider_attempt_details', [None])[0]))
    print('  candidate_metadata[0]: ' + str(psl_list[0].get('candidate_metadata', [None])[0])[:200] if psl_list[0].get('candidate_metadata') else 'none')
conn.close()
