import json
import sqlite3
ETIDS = ['edit-local-4b49e4d294', 'edit-local-b0187c6d33']
BATCH_MAP = {
    'edit-local-4b49e4d294': 'edit-batch-2e9dc62a6c73',
    'edit-local-b0187c6d33': 'edit-batch-stranger_education',
}
conn = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')
for etid in ETIDS:
    cur2 = conn.execute('SELECT task_id, payload_json FROM tasks WHERE task_id = ?', (etid,))
    rows = cur2.fetchall()
    if not rows:
        continue
    payload = json.loads(rows[0][1] or '{}')
    outputs = payload.get('outputs') or {}
    qr_str = outputs.get('quality_report')
    qr = json.loads(qr_str)
    print(f'=== {etid} ===')
    print(f'  transcript_accuracy_gate.method: {qr.get("transcript_accuracy_gate", {}).get("method")}')
    print(f'  transcript_accuracy_gate.passed: {qr.get("transcript_accuracy_gate", {}).get("passed")}')
    print(f'  transcript_accuracy_gate.raw_asr_equals_reviewed: {qr.get("transcript_accuracy_gate", {}).get("raw_asr_equals_reviewed")}')
    bid = BATCH_MAP.get(etid)
    if not bid:
        continue
    cur3 = conn.execute('SELECT payload_json FROM video_editor_batches WHERE batch_id = ?', (bid,))
    rows3 = cur3.fetchall()
    if not rows3:
        continue
    bp = json.loads(rows3[0][0])
    for item in bp.get('items') or []:
        if item.get('edit_task_id') == etid:
            pp = item.get('provider_payload') or {}
            le = pp.get('local_export') or {}
            inner_qr = le.get('quality_report') or {}
            print(f'  inner.visual_window_plan: {bool(inner_qr.get("visual_window_plan"))}')
            print(f'  inner.target_real_coverage_seconds: {inner_qr.get("target_real_coverage_seconds")}')
            print(f'  inner.current_real_coverage_seconds: {inner_qr.get("current_real_coverage_seconds")}')
            print(f'  inner.coverage_deficit_seconds: {inner_qr.get("coverage_deficit_seconds")}')
            print(f'  inner.unresolved_semantic_windows: {bool(inner_qr.get("unresolved_semantic_windows"))}')
            psl = inner_qr.get('provider_search_log') or []
            if psl:
                p0 = psl[0]
                print(f'  psl[0].local_pool_count: {p0.get("local_pool_count")}')
                print(f'  psl[0].semantic_candidate_count: {p0.get("semantic_candidate_count")}')
                print(f'  psl[0].accepted_local_candidate_count: {p0.get("accepted_local_candidate_count")}')
                print(f'  psl[0].projected_real_coverage_ratio: {p0.get("projected_real_coverage_ratio")}')
