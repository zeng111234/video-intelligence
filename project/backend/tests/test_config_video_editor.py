"""配置加载的进程级回归测试。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_invalid_video_editor_provider_mode_explains_valid_values() -> None:
    environment = os.environ.copy()
    environment["VIDEO_EDITOR_PROVIDER_MODE"] = "production"

    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "VIDEO_EDITOR_PROVIDER_MODE 仅支持 sandbox 或 aliyun。" in result.stderr
