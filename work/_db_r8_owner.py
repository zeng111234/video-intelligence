import json
import sqlite3
conn = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')
# 找 r8 批次的 source_id / item_id
cur = conn.execute("SELECT batch_id, payload_json FROM video_editor_batches WHERE batch_id = ?", ('edit-batch-2e9dc62a6c73',))
row = cur.fetchone()
payload = json.loads(row[1])
items = payload.get('items') or []
item = items[0]
print('item_id:', item.get('item_id'))
print('source_id:', item.get('source_id'))
print('owner:', payload.get('owner_code') or payload.get('desktop_owner') or 'unknown')
print('all payload top-level keys:', list(payload.keys())[:20])
