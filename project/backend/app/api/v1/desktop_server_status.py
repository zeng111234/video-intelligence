"""Desktop fallback for the company-service status card.

When a company control plane is configured, the desktop middleware proxies this
path before it reaches this router.  A local-only installation still needs a
truthful response so the admin page does not turn an expected deployment state
into a 404 error.
"""
from __future__ import annotations

from fastapi import APIRouter, Security

from project.backend.app.core.security import require_admin_token

router = APIRouter(prefix="/api/v1/admin", tags=["desktop-admin"])


def _waiting_for_company_server() -> dict[str, object]:
    return {
        "enabled": False,
        "live_ready": False,
        "missing_configuration": ["company_server"],
    }


@router.get("/server-status")
def desktop_server_status(
    _admin: bool = Security(require_admin_token),
) -> dict[str, object]:
    """Report local readiness without pretending the company server is online."""

    return {
        "service": "local_only",
        "crawler": {
            "location": "customer_desktop",
            "billable": False,
            "server_provider_disabled": True,
        },
        "copywriting": _waiting_for_company_server(),
        "transcription": _waiting_for_company_server(),
        "video_editor": _waiting_for_company_server(),
        "avatar": _waiting_for_company_server(),
    }
