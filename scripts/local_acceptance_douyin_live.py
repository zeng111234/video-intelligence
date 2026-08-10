"""One-shot non-billable acceptance of Douyin's public multi-column search."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.adapters.douyin_browser_search import (  # noqa: E402
    LocalDouyinPublicSearchProvider,
)
from src.models import Platform  # noqa: E402


def main() -> int:
    provider = LocalDouyinPublicSearchProvider(
        enabled=True,
        profile_dir=Path("data/browser_profiles/douyin").resolve(),
        browser_channel="chrome",
        debug_port=19222,
        timeout_seconds=35,
    )
    capability = provider.capabilities()
    if not capability.enabled:
        raise RuntimeError(
            "抖音官网搜索依赖未就绪：" + "、".join(capability.missing_configuration)
        )

    session = provider.start_login_browser()
    page = provider.search(
        platform=Platform.DOUYIN,
        keyword="餐饮",
        published_after=datetime.now().astimezone() - timedelta(days=180),
        limit=30,
        idempotency_key="local-acceptance-douyin-live-20260810",
    )
    error_codes = [error.code or error.kind.value for error in page.errors]
    evidence = [item.evidence for item in page.items[:3]]
    output = {
        "ok": len(page.items) > 2 and not any(
            code in {
                "public_search_multi_column_unavailable",
                "public_search_multi_column_unconfirmed",
                "public_search_time_filter_unavailable",
                "public_search_time_filter_unconfirmed",
            }
            for code in error_codes
        ),
        "session_phase": session.phase,
        "requested_limit": 30,
        "returned_count": len(page.items),
        "error_codes": error_codes,
        "sample_evidence": evidence,
        "credits_used": 0,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
