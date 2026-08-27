import json
import sqlite3

THEMES = ['stranger_education', 'stranger_saas', 'stranger_logistics']
conn = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')

for theme_id in THEMES:
    bid = f'edit-batch-{theme_id}'
    cur = conn.execute("SELECT payload_json FROM video_editor_batches WHERE batch_id = ?", (bid,))
    row = cur.fetchone()
    if row is None:
        print(f'{theme_id}: NOT FOUND')
        continue
    payload = json.loads(row[0])
    item = payload['items'][0]
    etid = item.get('edit_task_id')
    job = item.get('job', {})
    qr_str = job.get('quality_report')
    print(f'\n=== {theme_id} ===')
    print(f'  edit_task_id: {etid}')
    print(f'  job.status: {job.get("status")}')
    print(f'  job.quality_report type: {type(qr_str).__name__}, len: {len(qr_str) if qr_str else 0}')
    if qr_str and qr_str != 'None':
        try:
            qr = json.loads(qr_str)
            print(f'  real_stock_broll: {qr.get("real_stock_broll_coverage_ratio")} ({qr.get("real_stock_broll_seconds")}s)')
            print(f'  generated_image: {qr.get("generated_image_coverage_ratio")}')
            print(f'  deterministic_card: {qr.get("deterministic_card_coverage_ratio")}')
            print(f'  effective: {qr.get("effective_visual_coverage_ratio")}')
            print(f'  visual_release_passed: {qr.get("visual_release_passed")}')
            print(f'  transcript_accuracy_gate.passed: {qr.get("transcript_accuracy_gate", {}).get("passed")}')
            print(f'  transcript_timing_gate.passed: {qr.get("transcript_timing_gate", {}).get("passed")}')
            print(f'  current_real_coverage_seconds: {qr.get("current_real_coverage_seconds")}')
            print(f'  coverage_deficit_seconds: {qr.get("coverage_deficit_seconds")}')
            print(f'  unresolved_window_count: {qr.get("unresolved_window_count")}')
        except json.JSONDecodeError as e:
            print(f'  JSON decode err: {e}')
            print(f'  first 200: {qr_str[:200]}')
conn.close()
