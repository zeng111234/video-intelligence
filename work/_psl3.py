import json
import sqlite3
ETIDS = ['edit-local-6e03f0e1cd', 'edit-local-0b06adfccb']
conn = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')
for etid in ETIDS:
    cur = conn.execute('SELECT task_id, payload_json FROM tasks WHERE task_id = ?', (etid,))
    payload = json.loads(cur.fetchone()[1])
    psl_str = payload.get('outputs', {}).get('quality_report', '')
    if isinstance(psl_str, str):
        psl = json.loads(psl_str)
    else:
        psl = psl_str
    psg = psl.get('provider_search_gate', {})
    print('=== ' + etid + ' ===')
    print('  provider_search_gate: ' + str(psg))
    psl_list = psl.get('provider_search_log', [])
    for i, e in enumerate(psl_list[:3]):
        line = '  psl[' + str(i) + ']: status=' + str(e.get('provider_status')) + ' provider=' + str(e.get('provider')) + ' attempt=' + str(e.get('provider_attempted'))
        print(line)
        for ad in e.get('provider_attempt_details', []):
            print('    detail: ' + str(ad))
    if len(psl_list) > 3:
        print('  ... (only first 3 shown)')
