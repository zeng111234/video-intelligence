import json
import sqlite3
ETIDS = ['edit-local-6e03f0e1cd', 'edit-local-0b06adfccb', 'edit-local-df9a50d53c', 'edit-local-4619aecf4c']
LABELS = ['r8 / 烧烤店(同源+新review)', 'stranger_education / 教培', 'stranger_saas / SaaS', 'stranger_logistics / 物流']
BATCH_MAP = {
    'edit-local-6e03f0e1cd': 'edit-batch-2e9dc62a6c73',
    'edit-local-0b06adfccb': 'edit-batch-stranger_education',
    'edit-local-df9a50d53c': 'edit-batch-stranger_saas',
    'edit-local-4619aecf4c': 'edit-batch-stranger_logistics',
}
conn = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')
for etid, label in zip(ETIDS, LABELS):
    cur = conn.execute('SELECT task_id, payload_json FROM tasks WHERE task_id = ?', (etid,))
    rows = cur.fetchall()
    if not rows:
        continue
    payload = json.loads(rows[0][1] or '{}')
    outputs = payload.get('outputs') or {}
    qr_str = outputs.get('quality_report')
    qr = json.loads(qr_str)
    print(f'\n=== {label} ===')
    print(f'  etid: {etid}')
    print(f'  batch: {BATCH_MAP[etid]}')
    print(f'  mp4_path: C:/Users/zeng/Desktop/video/data/video_edits/{etid}.mp4')
    print(f'  result_size: {payload.get("result_size_bytes")} bytes')
    print(f'  duration_seconds: {qr.get("duration_seconds")}')
    print(f'  --- 4 独立 coverage ---')
    print(f'  real_stock_broll_coverage_ratio: {qr.get("real_stock_broll_coverage_ratio")} (target 0.45-0.65)')
    print(f'  real_stock_broll_seconds: {qr.get("real_stock_broll_seconds")}')
    print(f'  generated_image_coverage_ratio: {qr.get("generated_image_coverage_ratio")}')
    print(f'  deterministic_card_coverage_ratio: {qr.get("deterministic_card_coverage_ratio")}')
    print(f'  effective_visual_coverage_ratio: {qr.get("effective_visual_coverage_ratio")}')
    print(f'  --- 字幕门 ---')
    acc = qr.get('transcript_accuracy_gate', {})
    print(f'  transcript_accuracy_gate.passed: {acc.get("passed")} (P0-6 fix: expects False when raw_asr==reviewed)')
    print(f'  transcript_accuracy_gate.method: {acc.get("method")}')
    print(f'  transcript_accuracy_gate.raw_asr_equals_reviewed: {acc.get("raw_asr_equals_reviewed")}')
    tmg = qr.get('transcript_timing_gate', {})
    print(f'  transcript_timing_gate.passed: {tmg.get("passed")}')
    tsg = qr.get('transcript_source_identity_gate', {})
    print(f'  transcript_source_identity_gate.passed: {tsg.get("passed")}')
    print(f'  --- 视觉门 ---')
    print(f'  visual_release_passed: {qr.get("visual_release_passed")} (false = 安全降级 + 不冒充通过)')
    print(f'  real_broll_event_count: {qr.get("real_broll_event_count")}')
    print(f'  real_stock_video_event_count: {qr.get("real_stock_video_event_count")}')
    print(f'  generated_image_event_count: {qr.get("generated_image_event_count")}')
conn.close()
