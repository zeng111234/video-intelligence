import json
import sqlite3
conn = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')
cur = conn.execute("SELECT batch_id, payload_json FROM video_editor_batches WHERE batch_id = ?", ('edit-batch-2e9dc62a6c73',))
row = cur.fetchone()
if row is None:
    print('not found')
else:
    payload = json.loads(row[1])
    items = payload.get('items') or []
    item = items[0]
    print('item_id:', item.get('item_id'))
    print('status:', item.get('status'))
    print('edit_task_id:', item.get('edit_task_id'))
    print('source_id:', item.get('source_id'))
    print('subtitle_segments count:', len(item.get('subtitle_segments') or []))
    print('review_confirmed_at:', item.get('review_confirmed_at'))
    print('review_snapshot keys:', list((item.get('review_snapshot') or {}).keys())[:20])
    print('review_snapshot.confirmed:', (item.get('review_snapshot') or {}).get('confirmed'))
    print('is_mock:', item.get('is_mock'))
    print('enabled_plan_step_ids:', item.get('enabled_plan_step_ids'))
    print('selected_title:', item.get('selected_title'))
    print('selected_bgm_id:', item.get('selected_bgm_id'))
