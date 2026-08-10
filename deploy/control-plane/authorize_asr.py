"""One-time in-container ASR billing authorization helper."""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
import urllib.request


def post(path: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:8080{path}",
        data=encoded,
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    password = sys.stdin.readline().rstrip("\r\n")
    cap = sys.stdin.readline().strip() or "0.20"
    if not password:
        print("未提供管理员密码。", file=sys.stderr)
        return 1
    try:
        login = post(
            "/api/v1/auth/admin-login",
            {"username": "admin", "password": password},
        )
        token = str(login["token"])
        payload = {"confirmed": True, "per_task_cap_cny": cap}
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]
        result = post(
            "/api/v1/provider/asr/admin/authorization",
            payload,
            {
                "X-Admin-Token": token,
                "Idempotency-Key": f"asr-admin-authorization-{fingerprint}",
            },
        )
    except (KeyError, ValueError, urllib.error.URLError) as exc:
        print(f"授权失败：{getattr(exc, 'reason', '请检查密码和服务器配置')}。", file=sys.stderr)
        return 1
    if not result.get("billing_authorized"):
        print("授权未生效，请检查阿里云配置。", file=sys.stderr)
        return 1
    print("云端转写费用授权已保存；本操作没有提交素材，也没有产生供应商费用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
