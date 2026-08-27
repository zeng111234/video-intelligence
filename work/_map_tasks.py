import json
import sqlite3

ETIDS = ['edit-local-b0187c6d33', 'edit-local-3bd5724a95', 'edit-local-9bde277eef']
conn = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')

for etid in ETIDS:
    cur = conn.execute("SELECT task_id, payload_json FROM tasks WHERE task_id = ?", (etid,))
    row = cur.fetchone()
    if row is None:
        print(f'{etid}: NOT FOUND')
        continue
    payload = json.loads(row[1] or '{}')
    outputs = payload.get('outputs') or {}
    qr_str = outputs.get('quality_report')
    print(f'\n=== {etid} ===')
    print(f'  status: {payload.get("status")}')
    print(f'  result_path: {payload.get("result_path")}')
    if qr_str and qr_str != 'None':
        try:
            qr = json.loads(qr_str)
            print(f'  real_stock_broll: {qr.get("real_stock_broll_coverage_ratio")} ({qr.get("real_stock_broll_seconds")}s)')
            print(f'  generated_image: {qr.get("generated_image_coverage_ratio")}')
            print(f'  deterministic_card: {qr.get("deterministic_card_coverage_ratio")}')
            print(f'  effective: {qr.get("effective_visual_coverage_ratio")}')
            print(f'  visual_release_passed: {qr.get("visual_release_passed")}')
            print(f'  transcript_accuracy.passed: {qr.get("transcript_accuracy_gate", {}).get("passed")}')
            print(f'  transcript_timing.passed: {qr.get("transcript_timing_gate", {}).get("passed")}')
            print(f'  current_real_coverage_seconds: {qr.get("current_real_coverage_seconds")}')
            print(f'  coverage_deficit_seconds: {qr.get("coverage_deficit_seconds")}')
            print(f'  unresolved_window_count: {qr.get("unresolved_window_count")}')
            print(f'  duration_seconds: {qr.get("duration_seconds")}')
        except json.JSONDecodeError as e:
            print(f'  JSON err: {e}')
    else:
        print(f'  quality_report: {qr_str} (empty/None)')
conn.close()
