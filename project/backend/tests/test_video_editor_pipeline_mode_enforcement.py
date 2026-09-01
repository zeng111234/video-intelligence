"""P0-收口 2026-08-31: pipeline_mode=adaptive_fine_cut_v1 强校验.

Empty steps, unknown pipeline modes, and the legacy subtitle-only
path are all rejected with HTTP 422.  The frontend MUST set the new
field; the default exists only as a safety net so callers that
already pass the field are unaffected.
"""
from fastapi import HTTPException
import pytest

from project.backend.app.api.v1.video_editor import (
    VideoEditConfigRequest,
    VideoEditStepRequest,
    _build_edit_config,
)


def _step(kind: str = "subtitle") -> VideoEditStepRequest:
    return VideoEditStepRequest(kind=kind, params={})


def test_default_pipeline_mode_is_adaptive_fine_cut_v1():
    cfg = VideoEditConfigRequest(steps=[_step()])
    # P0-收口 2026-08-31: the default MUST be the only accepted value.
    assert cfg.pipeline_mode == "adaptive_fine_cut_v1"


def test_adaptive_fine_cut_v1_with_steps_passes():
    result = _build_edit_config(
        VideoEditConfigRequest(
            steps=[_step(kind="subtitle"), _step(kind="ai_subtitle")],
        )
    )
    assert result is not None
    assert len(result.steps) == 2


def test_empty_steps_rejected():
    """Empty steps list = legacy subtitle-only path = VISUAL_PIPELINE_NOT_EXECUTED."""
    with pytest.raises(HTTPException) as exc:
        _build_edit_config(VideoEditConfigRequest(steps=[]))
    assert exc.value.status_code == 422
    assert "steps" in str(exc.value.detail)


def test_unknown_pipeline_mode_rejected():
    with pytest.raises(HTTPException) as exc:
        _build_edit_config(
            VideoEditConfigRequest(
                pipeline_mode="legacy_subtitle_only",
                steps=[_step()],
            )
        )
    assert exc.value.status_code == 422
    assert "pipeline_mode" in str(exc.value.detail)


def test_legacy_field_name_also_rejected():
    """No path back to the old behaviour: a caller that omits steps
    AND names anything other than adaptive_fine_cut_v1 must fail."""
    with pytest.raises(HTTPException) as exc:
        _build_edit_config(
            VideoEditConfigRequest(pipeline_mode="auto", steps=[])
        )
    assert exc.value.status_code == 422
