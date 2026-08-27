import sqlite3, json
con = sqlite3.connect('file:C:/Users/zeng/Desktop/video/data/video_intelligence.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row
rows = con.execute("""
    SELECT video_id, platform, title, source_url
    FROM candidates
    WHERE source_url IS NOT NULL AND source_url != ''
    ORDER BY published_at DESC
    LIMIT 5
""").fetchall()
for r in rows:
    print(json.dumps(dict(r), ensure_ascii=False))
