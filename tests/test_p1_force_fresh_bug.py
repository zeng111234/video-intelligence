"""P1: 缺口驱动 force_fresh 集成行为测试（recovery goal §9 第一条实际执行动作）。

只测行为，不读源码结构。测试数据用通用"online education platform" 场景，
与 r8 "烧烤 顾客 回头客" 样片完全无关。

业务规则：

  1. 正常本地匹配已满足覆盖率 → 0 次联网（缓存命中走本地方案，不发 provider）。
  2. 覆盖不足时 → 恰好 1 次以 force_fresh=True 请求追加素材（不重用 cache）。
  3. provider 失败时 → 最终仍保留原本地匹配，真实覆盖率不下降。

实施约束：
  - patch workflow 模块实际导入的符号（不在测试里再读源码）
  - 用 tmp_path 构造可通过 local_broll_is_real 的"真实"测试素材，但本测试里直接
    mock local_broll_is_real 为 True（更轻、与素材本身解耦）
  - 用 spy class 记录 provider.search_and_cache 的调用次数和参数
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import pytest

from src.services import video_editor_workflow as wf
from src.services.stock_broll_provider import StockBrollResult
from src.services.video_editor_workflow import VideoEditorWorkflowService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_confirmed_video_asset(
    *, asset_id: str, keywords: list[str], duration: float = 8.0
) -> dict[str, Any]:
    """构造一个 confirmed 状态、媒体类型为 video 的资产。"""
    return {
        "asset_id": asset_id,
        "media_kind": "video",
        "media_type": "video/mp4",
        "source_provider": "pexels",
        "asset_origin": "stock_video_asset",
        "authorization_status": "confirmed",
        "publish_licensed": True,
        "keywords": keywords,
        "chinese_concepts": [],
        "provider_tags": keywords,
        "name": f"asset {asset_id}",
        "duration_seconds": duration,
    }


class _ProviderSpy:
    """替换 StockBrollProvider，记录 search_and_cache 调用。"""

    def __init__(self, *, fail: bool = False, returned_assets: list | None = None) -> None:
        self.directory = None
        self.calls: list[dict[str, Any]] = []
        self.fail = fail
        self.returned_assets = returned_assets or []

    # workflow 内部 `provider = StockBrollProvider(self._visual_asset_directory())`
    def __init_subclass__(cls, **kwargs):  # pragma: no cover
        super().__init_subclass__(**kwargs)

    @classmethod
    def install(
        cls,
        monkeypatch: pytest.MonkeyPatch,
        *,
        fail: bool = False,
        returned_assets: list | None = None,
    ) -> "_ProviderSpy":
        spy = cls(fail=fail, returned_assets=returned_assets)

        def _factory(directory):
            spy.directory = directory
            return spy

        monkeypatch.setattr(wf, "StockBrollProvider", _factory)
        return spy

    def search_and_cache(
        self, query, max_results: int = 8, **kwargs
    ) -> StockBrollResult:
        self.calls.append(
            {
                "query": query,
                "max_results": max_results,
                "force_fresh": kwargs.get("force_fresh", False),
                "kwargs": {k: v for k, v in kwargs.items() if k != "force_fresh"},
            }
        )
        if self.fail:
            raise RuntimeError("simulated Pexels/Pixabay failure")
        return StockBrollResult(
            "ready" if self.returned_assets else "unavailable",
            "pexels" if self.returned_assets else None,
            list(self.returned_assets),
            None if self.returned_assets else "no_video_results",
            1,
            len(self.returned_assets),
        )


@pytest.fixture
def patched_workflow(monkeypatch: pytest.MonkeyPatch):
    """把 workflow 内部使用的所有外部符号 patch 成可观测版本。

    Returns a namespace with:
      - service: minimal VideoEditorWorkflowService 实例
      - local_asset: 构造好的 confirmed 本地视频素材
      - shot_plan: 100s 长口播的 shot 计划（3 个 stride 选中）
      - provider: 替换好的 spy（None 表示测试自己安装）
    """
    # 与 r8 烧烤样片完全无关的通用教育/数据场景
    local_asset = _make_confirmed_video_asset(
        asset_id="local-broll-education-001",
        keywords=["online", "education", "platform", "dashboard"],
        duration=8.0,
    )

    # mock 本地语义匹配 ——
    # 模拟真实行为：仅当 pool 非空时返回 local_asset；pool 为空（fallback 到 generated
    # 池且 generated 为空）时返回 None。生产中 include_generated_images=False 时
    # generated 列表为空，因此第二次 fallback 调用应得 None。
    def _fake_match(query, pool, **kwargs):
        if not pool:
            return (None, 0, 0) if kwargs.get("return_counts") else None
        if kwargs.get("return_counts"):
            return (local_asset, 1, 1)
        return local_asset

    monkeypatch.setattr(wf, "match_local_visual_asset", _fake_match)

    # mock local_broll_is_real → True（让素材通过门）
    monkeypatch.setattr(wf, "local_broll_is_real", lambda _path, _meta: True)

    # mock _build_visual_request → 返回带 search_queries 的结构
    def _fake_build_vr(_shot, _transcript_segments):
        return {
            "search_queries": ["online education platform dashboard"],
            "transcript_text": "online education platform dashboard",
            "visual_type": "stock",
            "source_segment_indices": [0],
        }

    monkeypatch.setattr(wf, "_build_visual_request", _fake_build_vr)

    # mock query_visual_concepts → 非空列表
    monkeypatch.setattr(wf, "query_visual_concepts", lambda _q: ["online education"])

    # mock visual_search_query → 用 input 本身（避免依赖额外语义归一化）
    monkeypatch.setattr(wf, "visual_search_query", lambda q: q)

    # 构造最小 service（不连 DB / 不连 backend）
    service = VideoEditorWorkflowService.__new__(VideoEditorWorkflowService)

    # mock list_visual_assets 实例方法
    monkeypatch.setattr(
        service, "list_visual_assets", lambda _kind=None: [local_asset]
    )

    # mock _visual_asset_directory
    service._visual_asset_directory = lambda: Path("work/visual_assets_test")  # type: ignore[attr-defined]

    # 构造 100s long-form shot_plan，3 个 stride shot（每 shot 12.5s）
    shots = [
        {
            "shot_id": f"shot-{i:02d}",
            "role": "A-roll",
            "duration_seconds": 12.5,
            "timeline_start": 12.5 * i,
            "timeline_end": 12.5 * (i + 1),
            "source_start": 0,
            "source_end": 1,
        }
        for i in range(8)
    ]
    shot_plan = {
        "shots": shots,
        "timeline_duration_seconds": 100.0,
        "title": "online education platform",
    }

    return _WFHarness(service=service, local_asset=local_asset, shot_plan=shot_plan)


class _WFHarness:
    def __init__(self, *, service, local_asset, shot_plan):
        self.service = service
        self.local_asset = local_asset
        self.shot_plan = shot_plan

    @property
    def selected_shot_ids(self) -> list[str]:
        """复现 L6309-L6320 `_auto_bind_release_broll_assets` 内部的 `shots` 过滤：
        - role == "A-roll"
        - duration_seconds >= 1.2
        - timeline_start > 0  ← 这条排除了 shot-00 (timeline_start=0)

        然后按 L6615-L6619 走 selected_ids 的实际子集。
        """
        eligible = [
            s for s in self.shot_plan["shots"]
            if s.get("role") == "A-roll"
            and float(s.get("duration_seconds") or 0) >= 1.2
            and float(s.get("timeline_start") or 0) > 0
        ]
        return [s["shot_id"] for s in eligible]


def _invoke(harness: _WFHarness) -> dict[str, dict[str, Any]]:
    """调用 _auto_bind_release_broll_assets，返回 bindings。"""
    return harness.service._auto_bind_release_broll_assets(
        harness.shot_plan,
        transcript_segments=[
            {
                "start": 0,
                "end": 100,
                "text": "online education platform dashboard",
                "words": [],
            }
        ],
        include_generated_images=False,
    )


# ---------------------------------------------------------------------------
# 测试 A：本地匹配已满足覆盖率 → 0 次联网
# ---------------------------------------------------------------------------


def test_zero_network_when_local_cache_satisfies_coverage(
    monkeypatch: pytest.MonkeyPatch, patched_workflow: _WFHarness
) -> None:
    """当本地匹配累计时长 >= 0.45 * selected_total 时，工作流不应调 provider。

    selected_shots 在生产里先按 `timeline_start > 0` 过滤，再按 stride 取 3 个，
    本测试场景的阈值为 0.45 × 37.5s = 16.875s。让 fake_match 返回
    50s 素材即可让首个 shot 进入 binding 循环时累计满足阈值。
    """
    rich_asset = _make_confirmed_video_asset(
        asset_id="local-broll-rich",
        keywords=["online", "education", "platform", "dashboard"],
        duration=50.0,
    )
    monkeypatch.setattr(wf, "match_local_visual_asset",
        lambda q, _p, **kw: (rich_asset, 1, 1) if kw.get("return_counts") else rich_asset)

    spy = _ProviderSpy.install(monkeypatch)
    bindings = _invoke(patched_workflow)

    assert spy.calls == [], (
        f"本地匹配已满足覆盖率时应 0 次联网，实际调用了 {len(spy.calls)} 次："
        f"{spy.calls}"
    )
    assert bindings, f"应保留本地匹配，bindings 不应为空，实际 {bindings}"


# ---------------------------------------------------------------------------
# 测试 B：覆盖不足时 → 恰好 1 次且 force_fresh=True
# ---------------------------------------------------------------------------


def test_exactly_one_force_fresh_call_when_coverage_deficit(
    monkeypatch: pytest.MonkeyPatch, patched_workflow: _WFHarness
) -> None:
    """覆盖不足时，恰好调用 1 次 search_and_cache 且 force_fresh=True。"""
    # 默认 patched_workflow 的 local_asset duration=8s
    # 0.45 * 37.5s = 16.875s，8s < 16.875 → 缺口触发
    spy = _ProviderSpy.install(monkeypatch)
    _invoke(patched_workflow)

    assert len(spy.calls) == 1, (
        f"覆盖不足时应恰好 1 次联网，实际 {len(spy.calls)} 次：{spy.calls}"
    )
    assert spy.calls[0]["force_fresh"] is True, (
        f"覆盖不足触发的联网必须带 force_fresh=True，实际 kwargs: {spy.calls[0]}"
    )


# ---------------------------------------------------------------------------
# 测试 C：provider 失败时 → 保留原匹配，真实覆盖率不下降
# ---------------------------------------------------------------------------


def test_local_match_preserved_when_provider_returns_no_result(
    monkeypatch: pytest.MonkeyPatch, patched_workflow: _WFHarness
) -> None:
    """provider 返回无结果时，force_fresh 的第一个 shot 应回退到原匹配。

    StockBrollProvider 会把网络异常转换为 unavailable 结果而不是继续抛出，
    所以生产回退门必须覆盖“正常返回但无可接受新素材”这一常见路径。
    """
    spy = _ProviderSpy.install(monkeypatch)
    bindings = _invoke(patched_workflow)

    # 1) 触发 force_fresh 的那个 shot（这里按 stride 第一个 = shot-00）必须有 binding
    force_fresh_shot_id = patched_workflow.selected_shot_ids[0]
    assert force_fresh_shot_id in bindings, (
        f"触发 force_fresh 的 {force_fresh_shot_id} 在 provider 失败时应回退到原本地匹配，"
        f"实际 bindings 缺该 shot。全部 binding: {sorted(bindings.keys())}"
    )

    # 2) 该 binding 必须指向原本地匹配（不是 None / 不是新创建）
    forced_binding = bindings[force_fresh_shot_id]
    assert forced_binding.get("asset_id") == "local-broll-education-001", (
        f"{force_fresh_shot_id} 应绑定原本地匹配 'local-broll-education-001'，"
        f"实际: {forced_binding}"
    )

    # 3) provider 真实被调用过 1 次（证明确实走了 force_fresh 路径，而不是测试
    #    根本没到 provider）
    assert len(spy.calls) == 1, (
        f"期望恰好 1 次 provider 调用以证明走了 force_fresh 路径，"
        f"实际 {len(spy.calls)} 次"
    )
