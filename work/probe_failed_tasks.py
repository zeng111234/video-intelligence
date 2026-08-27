import sqlite3, json
con = sqlite3.connect('C:/Users/zeng/Desktop/video/data/video_intelligence.db')
con.row_factory = sqlite3.Row
rows = con.execute("""
    SELECT task_id, payload_json
    FROM tasks
    WHERE payload_json LIKE '%cloud_asr_failed%'
    ORDER BY created_at DESC
    LIMIT 5
""").fetchall()
print(f"匹配 cloud_asr_failed 的: {len(rows)} 条")
rows = con.execute("""
    SELECT task_id, payload_json
    FROM tasks
    WHERE payload_json LIKE '%aliyun_fun_asr%'
    ORDER BY created_at DESC
    LIMIT 5
""").fetchall()
print(f"匹配 aliyun_fun_asr 的: {len(rows)} 条")
for r in rows:
    p = json.loads(r[1])
    print(f"  {r[0]} status={p.get('status')} err={repr((p.get('error_message') or '')[:200])}")
import json
for r in rows:
    d = dict(r)
    pj = d.get('payload_json', '{}')
    try:
        p = json.loads(pj)
    except Exception:
        p = {}
    print("---")
    print(f"  task_id: {d.get('task_id')}")
    print(f"  status: {p.get('status')}")
    print(f"  error_message: {(p.get('error_message') or '')[:500]}")
    print(f"  provider_status: {p.get('provider_status')}")
    print(f"  provider_job_id: {p.get('provider_job_id')}")
    print(f"  stage: {p.get('stage')}")
