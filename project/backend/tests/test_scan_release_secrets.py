from __future__ import annotations

import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "scan_release_secrets.py"
)
spec = spec_from_file_location("scan_release_secrets", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
scanner = module_from_spec(spec)
sys.modules[spec.name] = scanner
spec.loader.exec_module(scanner)


def _git(tmp_path: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )


def test_scan_compares_ignored_env_value_without_printing_it(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "COPYWRITING_API_KEY=local-secret-value-123\n", encoding="utf-8"
    )
    (tmp_path / "safe.py").write_text("print('safe')\n", encoding="utf-8")
    _git(tmp_path, "add", ".gitignore", "safe.py")
    monkeypatch.delenv("COPYWRITING_API_KEY", raising=False)

    configured_count, matches = scanner.scan(tmp_path)
    assert configured_count >= 1
    assert matches == []

    (tmp_path / "safe.py").write_text(
        "value = 'local-secret-value-123'\n", encoding="utf-8"
    )
    _, matches = scanner.scan(tmp_path)
    assert matches == ["COPYWRITING_API_KEY@root-env:safe.py"]
    assert all("local-secret-value-123" not in match for match in matches)
