import sqlite3, json
con = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')
con.row_factory = sqlite3.Row

# 1. 最近 10 条 aliyun_fun_asr 任务, 看 segments 是否有真实内容
rows = con.execute("""
SELECT task_id, payload_json
FROM tasks
WHERE payload_json LIKE '%aliyun_fun_asr%'
ORDER BY created_at DESC
LIMIT 10
""").fetchall()

print(f"=== 最近 10 条 aliyun_fun_asr 任务 ===")
for r in rows:
    p = json.loads(r[1])
    segs = p.get('segments') or []
    seg_count = len(segs) if isinstance(segs, list) else 0
    total_chars = sum(len(str(s.get('text',''))) for s in segs) if isinstance(segs, list) else 0
    print(f"  {r[0]} status={p.get('status'):8s} segs={seg_count:3d} chars={total_chars:5d} stage={p.get('stage')!r}")

# 2. 失败任务最近 10 条
print(f"\n=== 最近 10 条 status=failed 的 transcription 任务 ===")
rows2 = con.execute("""
SELECT task_id, payload_json
FROM tasks
WHERE payload_json LIKE '%transcript-%'
  AND (payload_json LIKE '%"status": "failed"%' OR payload_json LIKE '%cloud_asr_failed%')
ORDER BY created_at DESC
LIMIT 10
""").fetchall()
for r in rows2:
    p = json.loads(r[1])
    err = p.get('error_message','')
    if not err: continue
    print(f"  {r[0]} err={err[:150]!r}")
