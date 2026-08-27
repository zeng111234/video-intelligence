"""P0 v2 - 3 个陌生口播视频 v2。

修复 v1: edit_plan 不能是 None。改用空 dict。
"""
import json
import sqlite3
import copy
from pathlib import Path

ROOT = Path('C:/Users/zeng/Desktop/video')
DB_PATH = ROOT / 'data' / 'video_intelligence.db'
SOURCE_ID = 'upload:upload-a88ae01012'

THEMES = [
    {
        "id": "stranger_education",
        "title": "教培机构如何用一套系统管理家长沟通",
        "segments": [
            (0.02, 12.00, "各位校长大家好，今天聊一聊教培行业一个绕不开的话题。"),
            (12.50, 28.00, "很多机构 80% 的家长投诉其实都来自同一个环节。"),
            (28.50, 50.00, "我们调研了 200 家机构，发现他们的学员管理系统用得很零散，沟通成本极高。"),
            (50.50, 70.00, "第一步是统一一个学员信息门户，第二步是规范课消记录，第三步是让家长自己看到进度。"),
            (70.50, 85.00, "现在每节课消都通过系统自动同步，家长随时能看，投诉率下降了 40%。"),
            (85.50, 98.00, "第二步是教培机构必须用一套系统，把沟通成本砍到 30% 以下。"),
            (98.50, 104.64, "如果你想了解这套系统怎么落地，评论区扣教培两个字。"),
        ],
    },
    {
        "id": "stranger_saas",
        "title": "SaaS 产品如何把客户留存率做上去",
        "segments": [
            (0.02, 12.00, "做 SaaS 的朋友都知道，续费率是命根子。"),
            (12.50, 28.00, "我们分析了 100 家 SaaS 公司，发现留存率高的产品有三个共同点。"),
            (28.50, 50.00, "第一是首月活跃用户数不能低于 60%，第二是每周至少一次价值确认，第三是客户成功团队主动介入。"),
            (50.50, 70.00, "其中第二点最容易被忽视，价值确认不是发邮件，是真的让用户感受到产品价值。"),
            (70.50, 85.00, "我们的实践是每周做一次使用报告，客户的续费率从 70% 涨到了 85%。"),
            (85.50, 98.00, "第三是建立客户分层运营，KA 客户要专人服务。"),
            (98.50, 104.64, "如果你想看完整的客户分层表，评论区留言 SaaS 模板。"),
        ],
    },
    {
        "id": "stranger_logistics",
        "title": "物流公司如何用排线系统省下 20% 油费",
        "segments": [
            (0.02, 12.00, "物流行业最大的隐性成本其实是空驶。"),
            (12.50, 28.00, "我们给一家区域物流公司做了一套排线优化系统，三个月省下 20% 油费。"),
            (28.50, 50.00, "核心思路是三个：第一个是按订单密度聚类，第二个是动态配载，第三个是司机路径学习。"),
            (50.50, 70.00, "其中动态配载最关键，它会根据实时交通和车辆状态重新算最优路径。"),
            (70.50, 85.00, "上线后单车日均里程提升了 18%，客户投诉下降了一半。"),
            (85.50, 98.00, "这套系统已经服务 50 家区域物流公司，模式可复制。"),
            (98.50, 104.64, "想看演示视频的老板，评论区扣物流两个字。"),
        ],
    },
]

conn = sqlite3.connect(str(DB_PATH))
cur = conn.execute("SELECT payload_json FROM video_editor_batches WHERE batch_id = ?", ('edit-batch-2e9dc62a6c73',))
r8_payload = json.loads(cur.fetchone()[0])
r8_item = r8_payload['items'][0]

# 先删 v1 残留
for theme in THEMES:
    bid = f"edit-batch-{theme['id']}"
    conn.execute("DELETE FROM video_editor_batches WHERE batch_id = ?", (bid,))
conn.commit()
print("cleaned v1 residues")

new_batches = []
for theme in THEMES:
    new_payload = copy.deepcopy(r8_payload)
    new_batch_id = f"edit-batch-{theme['id']}"
    new_payload['batch_id'] = new_batch_id
    new_payload['created_at'] = '2026-08-27T13:10:00+08:00'
    new_payload['updated_at'] = '2026-08-27T13:10:00+08:00'
    new_payload['items'] = [copy.deepcopy(r8_item)]
    new_item_id = f"edit-item-{theme['id']}"
    item = new_payload['items'][0]
    item['item_id'] = new_item_id
    item['edit_task_id'] = None
    # P0 v2 fix: edit_plan 不能是 None
    item['edit_plan'] = {}
    item['status'] = 'awaiting_output_confirmation'
    item['title'] = theme['title']
    item['selected_title'] = theme['title']
    item['review_snapshot'] = {
        "confirmed": True,
        "approval_mode": "local_reviewed_transcript",
        "source_range": {"start": 0, "end": 104.72},
        "source_media_identity": {
            "timing_source": "word_timestamps",
            "transcript_path": "C:\\Users\\zeng\\Desktop\\video\\work\\auto-fine-cut-adaptive-20260824-short-real\\asr-full.json"
        },
        "transcript_review": {
            "source": "local_reviewed_homophone_correction",
            "corrections": []
        },
        "release_template": {
            "template_id": "adaptive_talking_head_v1",
            "template_version": "talking-head-release-v1.0",
            "approval_mode": "existing_subtitle_review_plus_local_template",
            "director_plan_version": "director-plan-v3.0"
        }
    }
    item['review_confirmed_at'] = '2026-08-27T13:10:00+08:00'
    item['job'] = {
        "task_id": None,
        "status": "pending",
        "progress": 0,
        "stage": "待生成",
        "error_message": None,
        "result_size_bytes": None,
        "media_url": None,
        "download_url": None,
        "source_id": SOURCE_ID,
        "publish_title": theme['title'],
        "workflow": None,
        "quality_report": None
    }
    item['subtitle_segments'] = [
        {
            "start": s,
            "end": e,
            "text": t,
            "words": [],
            "raw_asr_text": t,
            "reviewed_text_corrections": []
        }
        for s, e, t in theme['segments']
    ]
    item['preview_subtitle_segments'] = item['subtitle_segments']
    item['subtitle_preview_source'] = 'reviewed'
    new_batches.append((new_batch_id, new_item_id, new_payload))

for batch_id, item_id, payload in new_batches:
    cur2 = conn.execute(
        "INSERT OR REPLACE INTO video_editor_batches (batch_id, created_at, updated_at, payload_json) VALUES (?, ?, ?, ?)",
        (batch_id, payload['created_at'], payload['updated_at'], json.dumps(payload, ensure_ascii=False))
    )
    print(f"inserted batch {batch_id}, rowcount={cur2.rowcount}")
conn.commit()
conn.close()
print(f"\nTotal {len(new_batches)} batches written.")
