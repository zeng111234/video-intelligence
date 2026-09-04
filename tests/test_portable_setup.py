from __future__ import annotations

import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOTS = (
    PROJECT_ROOT / "scripts",
    PROJECT_ROOT / "src",
    PROJECT_ROOT / "project" / "backend" / "app",
    PROJECT_ROOT / "project" / "frontend" / "src",
)
RUNTIME_ENTRYPOINTS = (
    PROJECT_ROOT / "start.bat",
    PROJECT_ROOT / "project" / "backend" / "start.ps1",
    PROJECT_ROOT / "project" / "frontend" / "start.ps1",
)


def _runtime_files() -> list[Path]:
    files = list(RUNTIME_ENTRYPOINTS)
    for root in RUNTIME_ROOTS:
        files.extend(
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix.casefold()
            in {".py", ".ps1", ".bat", ".cmd", ".js", ".mjs", ".ts", ".tsx"}
        )
    return files


def test_runtime_does_not_contain_hardcoded_windows_user_profile() -> None:
    offenders: list[str] = []
    user_profile = re.compile(r"[a-z]:[\\/]+users[\\/]+[^\\/]+", re.IGNORECASE)

    for path in _runtime_files():
        if user_profile.search(path.read_text(encoding="utf-8-sig", errors="ignore")):
            offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert offenders == []


def test_windows_setup_uses_project_local_and_locked_dependencies() -> None:
    setup = (PROJECT_ROOT / "scripts" / "setup_windows.ps1").read_text(
        encoding="utf-8-sig"
    )
    startup = (PROJECT_ROOT / "scripts" / "start_all_services.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert '".venv"' in setup
    assert " ci --no-audit --no-fund" in setup
    assert "package-lock.json" in setup
    assert 'PackageId "Python.Python.3.12"' in setup
    assert 'PackageId "OpenJS.NodeJS.LTS"' in setup
    assert "--disable-interactivity" in setup
    assert '".venv\\Scripts\\python.exe"' in startup
    assert 'StartCommand = "python"' not in startup
    assert 'requirements.txt' in setup
    assert 'project\\backend\\requirements.txt' in setup
    assert 'faster_whisper' in setup


def test_frontend_manifest_and_lockfile_stay_in_sync() -> None:
    frontend = PROJECT_ROOT / "project" / "frontend"
    manifest = json.loads((frontend / "package.json").read_text(encoding="utf-8"))
    lockfile = json.loads(
        (frontend / "package-lock.json").read_text(encoding="utf-8")
    )
    locked_root = lockfile["packages"][""]

    assert locked_root["dependencies"] == manifest["dependencies"]
    assert locked_root["devDependencies"] == manifest["devDependencies"]


def test_frontend_test_tools_match_vite_5_and_node_18() -> None:
    manifest = json.loads(
        (PROJECT_ROOT / "project" / "frontend" / "package.json").read_text(
            encoding="utf-8"
        )
    )
    dev_dependencies = manifest["devDependencies"]

    assert dev_dependencies["vite"] == "5.4.21"
    assert dev_dependencies["vitest"] == "3.2.7"
    assert dev_dependencies["jsdom"] == "24.1.3"
