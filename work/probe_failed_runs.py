import sqlite3, json
con = sqlite3.connect('file:C:/Users/zeng/Desktop/video/data/video_intelligence.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row
rows = con.execute("""
    SELECT run_id, keyword, status, created_at, updated_at, payload_json
    FROM pipeline_runs
    ORDER BY updated_at DESC
    LIMIT 3
""").fetchall()
for r in rows:
    d = dict(r)
    if d.get('error_message'):
        d['error_message'] = d['error_message'][:600]
    print(json.dumps(d, ensure_ascii=False, default=str))
