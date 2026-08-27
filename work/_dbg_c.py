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

service = VideoEditorWorkflowService.__new__(VideoEditorWorkflowService)
service.list_visual_assets = lambda k=None: [local_asset]
service._visual_asset_directory = lambda: Path("work/visual_assets_test")

shots = [
    {"shot_id": f"shot-{i:02d}", "role": "A-roll", "duration_seconds": 12.5,
     "timeline_start": 12.5*i, "timeline_end": 12.5*(i+1), "source_start": 0, "source_end": 1}
    for i in range(8)
]
shot_plan = {"shots": shots, "timeline_duration_seconds": 100.0, "title": "online education"}

class Spy:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
    def __call__(self, directory):
        return self
    def search_and_cache(self, query, max_results=8, **kwargs):
        self.calls.append({"q": query[:40]})
        if self.fail:
            raise RuntimeError("sim")
        return None

spy = Spy(fail=True)
wf.StockBrollProvider = spy

bindings = service._auto_bind_release_broll_assets(
    shot_plan,
    transcript_segments=[{"start": 0, "end": 100, "text": "online education platform dashboard", "words": []}],
    include_generated_images=False,
)

print("spy.calls:", len(spy.calls), spy.calls)
print("bindings count:", len(bindings), "keys:", sorted(bindings.keys()))
log = shot_plan.get("provider_search_log", [])
print("log count:", len(log))
for i, r in enumerate(log):
    fa = r.get("final_asset_id")
    ps = r.get("provider_status")
    fb = r.get("fallback_to_local")
    fr = r.get("force_fresh_reason")
    print("  log[", i, "]: final_asset_id=", fa, " status=", ps, " force_fresh=", fr, " fallback=", fb, sep="")
