from types import SimpleNamespace

from src.services.candidate_copy_probe import _has_detectable_copy


def test_copy_probe_requires_meaningful_recognised_text():
    assert _has_detectable_copy([SimpleNamespace(text="贴标机怎么选", avg_logprob=-0.2)])
    assert not _has_detectable_copy([SimpleNamespace(text="嗯", avg_logprob=-0.2)])
    assert not _has_detectable_copy([SimpleNamespace(text="贴标机怎么选", avg_logprob=-2.0)])
