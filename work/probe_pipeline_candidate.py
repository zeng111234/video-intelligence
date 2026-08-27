import sqlite3, json
con = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')
con.row_factory = sqlite3.Row

# 1. 找 production_batch 85a4e8 里 candidate 的 source_url
rows = con.execute("""
SELECT c.video_id, c.source_url, c.platform, c.title
FROM candidates c
WHERE c.video_id = 'bilibili-BV1bw8v69ELg'
""").fetchall()
for r in rows:
    print(dict(r))
print("---")

# 2. 找一个 production batch item 的实际 video_path / source_url
# 看 production_batches 表结构
print("production_batches columns:")
for r in con.execute("PRAGMA table_info(production_batches)"):
    print(" ", r[1], r[2])
