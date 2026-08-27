import json
import sqlite3
conn = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')
cur = conn.execute("SELECT payload_json FROM video_editor_batches WHERE batch_id = ?", ('edit-batch-2e9dc62a6c73',))
bp = json.loads(cur.fetchone()[0])
item = bp['items'][0]
rs = item.get('review_snapshot') or {}
print('review_snapshot keys:', list(rs.keys())[:15])
if 'broll' in rs:
    print('broll keys:', list(rs['broll'].keys()) if isinstance(rs['broll'], dict) else 'not dict')
    print('broll sample:', json.dumps(rs['broll'], ensure_ascii=False)[:500])
if 'brolls' in rs:
    print('brolls list len:', len(rs['brolls']))
    print('brolls[0] keys:', list(rs['brolls'][0].keys()) if rs['brolls'] else 'empty')
if 'broll_bindings' in rs:
    print('broll_bindings list len:', len(rs['broll_bindings']))
conn.close()
