from types import SimpleNamespace

from src.adapters.browser_window import reveal_browser_window


def test_reveal_browser_window_handles_missing_netstat_stdout(monkeypatch):
    monkeypatch.setattr(
        "src.adapters.browser_window.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=None),
    )

    assert reveal_browser_window(19222) is False
