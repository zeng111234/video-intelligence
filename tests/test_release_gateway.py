from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "deploy" / "release-gateway" / "videoinsight-release-gateway.sh"
INSTALLER = ROOT / "deploy" / "release-gateway" / "install.sh"
PREFLIGHT = ROOT / "scripts" / "test_videoinsight_deploy_access.ps1"
DEPLOY = ROOT / "scripts" / "deploy_videoinsight_release.ps1"


def test_gateway_is_scoped_to_videoinsight_paths() -> None:
    text = GATEWAY.read_text(encoding="utf-8")
    assert "/opt/videoinsight-control-plane" in text
    assert "/www/wwwroot/xmt.syszr.cn/desktop-updates" in text
    assert "videoinsight-control-plane.service" in text
    assert "docker" not in text.lower()
    assert "caddy" not in text.lower()
    assert "nginx" not in text.lower()
    assert "validate_staged_file" in text
    assert "sha256sum" in text
    assert "preflight.sh" in text
    assert "upgrade.sh" in text
    assert "verify.sh" in text


def test_gateway_installer_grants_only_the_gateway() -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    assert "visudo -cf" in text
    assert (
        "devuser ALL=(root) NOPASSWD: /usr/local/sbin/videoinsight-release-gateway"
        in text
    )
    assert "NOPASSWD: /bin/cp" not in text
    assert "NOPASSWD: /bin/mv" not in text
    assert "NOPASSWD: /usr/bin/systemctl" not in text


def test_local_preflight_disables_password_fallback_and_rejects_old_rules() -> None:
    text = PREFLIGHT.read_text(encoding="utf-8")
    for requirement in (
        "BatchMode=yes",
        "PasswordAuthentication=no",
        "KbdInteractiveAuthentication=no",
        "NumberOfPasswordPrompts=0",
        "videoinsight-release-gateway probe",
        "旧的过宽 sudo 权限",
    ):
        assert requirement in text


def test_deploy_orchestrator_is_fail_closed_and_verifies_public_state() -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    for requirement in (
        "test_videoinsight_deploy_access.ps1",
        "BatchMode=yes",
        "PasswordAuthentication=no",
        "NumberOfPasswordPrompts=0",
        "control_plane_sha256",
        "installer_sha256",
        "Get-Sha256Hex",
        "https://xmt.syszr.cn/health",
        "https://xmt.syszr.cn/desktop-updates/latest.json",
        "deploy-control-plane",
        "publish-desktop",
    ):
        assert requirement in text
