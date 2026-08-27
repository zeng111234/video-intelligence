import sys
sys.path.insert(0, ".")
from pathlib import Path
from src.services import video_editor_workflow as wf
from src.services.video_editor_workflow import VideoEditorWorkflowService
from tests.test_p1_force_fresh_bug import _make_confirmed_video_asset

local_asset = _make_confirmed_video_asset(
    asset_id="local-broll-education-001",
    keywords=["online", "education", "platform", "dashboard"],
    duration=8.0,
)

def fake_match(query, pool, **kwargs):
    if not pool:
        return (None, 0, 0) if kwargs.get("return_counts") else None
    return (local_asset, 1, 1) if kwargs.get("return_counts") else local_asset

wf.match_local_visual_asset = fake_match
wf.local_broll_is_real = lambda p, m: True
wf.query_visual_concepts = lambda q: ["online education"]
wf.visual_search_query = lambda q: q

def fake_build_vr(shot, ts):
    return {"search_queries": ["online education platform dashboard"], "transcript_text": "online education platform dashboard", "visual_type": "stock", "source_segment_indices": [0]}
wf._build_visual_request = fake_build_vr

# 加 print 到 binding 循环
orig = VideoEditorWorkflowService._auto_bind_release_broll_assets
import inspect
src = inspect.getsource(orig)
print("=== function source signature line ===")
for line in src.splitlines()[:3]:
    print(line)
