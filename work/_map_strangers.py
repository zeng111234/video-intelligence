"""Map task_id -> batch_id for 3 strangers."""
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
    result_path = item.get('job', {}).get('media_url') or item.get('result_media_url')
    quality = item.get('job', {}).get('quality_report') or {}
    if isinstance(quality, str):
        try:
            quality = json.loads(quality)
        except:
            quality = {}
    rsb_ratio = quality.get('real_stock_broll_coverage_ratio')
    rsb_seconds = quality.get('real_stock_broll_seconds')
    gi_ratio = quality.get('generated_image_coverage_ratio')
    dc_ratio = quality.get('deterministic_card_coverage_ratio')
    eff_ratio = quality.get('effective_visual_coverage_ratio')
    visual_release = quality.get('visual_release_passed')
    print(f'\n=== {theme_id} ===')
    print(f'  edit_task_id: {etid}')
    print(f'  mp4_path: C:/Users/zeng/Desktop/video/data/video_edits/{etid}.mp4')
    print(f'  real_stock_broll: {rsb_ratio} ({rsb_seconds}s)')
    print(f'  generated_image: {gi_ratio}')
    print(f'  deterministic_card: {dc_ratio}')
    print(f'  effective: {eff_ratio}')
    print(f'  visual_release_passed: {visual_release}')
conn.close()
