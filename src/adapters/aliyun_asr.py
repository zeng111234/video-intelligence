"""阿里云智能语音交互 —— 实时语音转写 REST API 适配器。

调用流程：
1. 通过 AccessKey 获取 NlsToken
2. 提交录音文件识别任务（CreateTask）
3. 轮询任务状态直到完成（QueryTask）
4. 下载识别结果

参考文档：
https://help.aliyun.com/document_detail/324929.html
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
import uuid
from base64 import b64encode
from datetime import datetime, timezone
from typing import Any

import httpx

from src.adapters.cloud_asr import (
    ASRResult,
    ASRSegment,
    CloudASRProvider,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_ASR_REGION = "cn-shanghai"
_ASR_ENDPOINT = f"https://nls-gateway.{_ASR_REGION}.aliyuncs.com"
_TOKEN_URL = f"https://nls-meta.{_ASR_REGION}.aliyuncs.com"
_CREATE_TASK_URL = f"{_ASR_ENDPOINT}/stream/v1/asr/file_upload"
_QUERY_TASK_URL = f"{_ASR_ENDPOINT}/stream/v1/asr/file_query"

_DEFAULT_TIMEOUT = 60.0
_POLL_INTERVAL = 2.0
_MAX_POLL_ATTEMPTS = 300


class AliyunASRError(RuntimeError):
    """阿里云 ASR 调用失败。"""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class AliyunASRProvider(CloudASRProvider):
    """阿里云录音文件识别 API 适配器。

    Parameters
    ----------
    access_key_id : str
        阿里云 AccessKey ID。
    access_key_secret : str
        阿里云 AccessKey Secret。
    app_key : str
        智能语音交互项目 AppKey。
    timeout : float
        HTTP 请求超时秒数。
    """

    def __init__(
        self,
        access_key_id: str,
        access_key_secret: str,
        app_key: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        if not access_key_id.strip():
            raise AliyunASRError("access_key_id 不能为空。", code="invalid_credential")
        if not access_key_secret.strip():
            raise AliyunASRError("access_key_secret 不能为空。", code="invalid_credential")
        if not app_key.strip():
            raise AliyunASRError("app_key 不能为空。", code="invalid_credential")

        self._ak = access_key_id.strip()
        self._sk = access_key_secret.strip()
        self._app_key = app_key.strip()
        self._timeout = timeout
        self._client = httpx.Client(timeout=timeout)

    # ------------------------------------------------------------------
    # CloudASRProvider 接口
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, str | bool | int | list[str]]:
        return {
            "provider_name": "aliyun_asr",
            "display_name": "阿里云语音识别",
            "mode": "cloud",
            "enabled": True,
            "supported_languages": ["zh", "en", "ja", "ko"],
            "max_audio_seconds": 3600,
            "max_file_size_mb": 500,
        }

    def transcribe(
        self,
        audio_path: str,
        *,
        language: str = "zh",
        hotwords: str = "",
    ) -> ASRResult:
        """调用阿里云录音文件识别 API。

        Parameters
        ----------
        audio_path : str
            音频文件路径（支持 WAV / PCM / MP3 / OGG 等）。
        language : str
            语言代码，映射到阿里云 app_key 对应的语言模型。
        hotwords : str
            热词，逗号分隔。

        Returns
        -------
        ASRResult
        """
        from pathlib import Path

        path = Path(audio_path)
        if not path.exists():
            raise AliyunASRError(f"音频文件不存在: {audio_path}", code="file_not_found")
        if path.stat().st_size == 0:
            raise AliyunASRError("音频文件为空。", code="empty_file")

        # 获取 Token
        token = self._get_token()

        # 上传文件并创建任务
        task_id = self._create_task(audio_path, token, hotwords)

        # 轮询结果
        raw_result = self._poll_result(task_id, token)

        # 解析结果
        return self._parse_result(raw_result, language)

    def close(self) -> None:
        """关闭 HTTP 客户端。"""
        self._client.close()

    def __enter__(self) -> AliyunASRProvider:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _sign_request(self, params: dict[str, str]) -> str:
        """计算阿里云 API 签名。"""
        sorted_params = sorted(params.items())
        query_string = urllib.parse.urlencode(sorted_params)
        string_to_sign = f"POST&%2F&{urllib.parse.quote(query_string, safe='')}"
        sign = hmac.new(
            (self._sk + "&").encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        return b64encode(sign).decode("utf-8")

    def _get_token(self) -> str:
        """获取 NlsToken。"""
        params = {
            "AccessKeyId": self._ak,
            "Action": "CreateToken",
            "Format": "JSON",
            "RegionId": _ASR_REGION,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": uuid.uuid4().hex,
            "SignatureVersion": "1.0",
            "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Version": "2019-02-28",
        }
        params["Signature"] = self._sign_request(params)
        resp = self._client.post(_TOKEN_URL, data=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("Code") != "200":
            raise AliyunASRError(
                f"获取 Token 失败: {data.get('Message', '未知错误')}",
                code="token_error",
            )
        return data["Data"]["Token"]["Id"]

    def _create_task(
        self,
        audio_path: str,
        token: str,
        hotwords: str = "",
    ) -> str:
        """上传音频文件并创建转写任务，返回 task_id。"""
        from pathlib import Path

        path = Path(audio_path)
        file_bytes = path.read_bytes()

        headers = {
            "X-NLS-Token": token,
        }

        form_data: dict[str, Any] = {
            "appkey": self._app_key,
            "file_link": "",
        }
        if hotwords:
            form_data["extra"] = json.dumps({"hotwords": hotwords})

        files = {
            "file": (path.name, file_bytes, "audio/wav"),
        }

        resp = self._client.post(
            _CREATE_TASK_URL,
            headers=headers,
            data=form_data,
            files=files,
        )
        resp.raise_for_status()
        data = resp.json()

        status_code = data.get("StatusCode")
        if status_code != 21050000:
            raise AliyunASRError(
                f"创建任务失败: {data.get('StatusText', '未知错误')} (code={status_code})",
                code="create_task_error",
            )
        task_id = data.get("TaskId")
        if not task_id:
            raise AliyunASRError("创建任务成功但未返回 TaskId。", code="no_task_id")
        return task_id

    def _poll_result(self, task_id: str, token: str) -> dict[str, Any]:
        """轮询任务状态直到完成。"""
        headers = {
            "X-NLS-Token": token,
        }
        params = {
            "appkey": self._app_key,
            "task_id": task_id,
        }

        for _ in range(_MAX_POLL_ATTEMPTS):
            resp = self._client.get(
                _QUERY_TASK_URL,
                headers=headers,
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

            status_code = data.get("StatusCode")
            if status_code == 21050001:
                # 任务仍在运行
                time.sleep(_POLL_INTERVAL)
                continue
            if status_code == 21050000:
                return data
            raise AliyunASRError(
                f"任务查询失败: {data.get('StatusText', '未知错误')} (code={status_code})",
                code="poll_task_error",
            )

        raise AliyunASRError(
            f"任务 {task_id} 超时未完成。", code="task_timeout",
        )

    def _parse_result(self, raw: dict[str, Any], language: str) -> ASRResult:
        """解析阿里云返回结果为 ASRResult。"""
        result_text = raw.get("Result", "")
        sentences: list[dict[str, Any]] = []

        if isinstance(result_text, str) and result_text:
            try:
                parsed = json.loads(result_text)
                if isinstance(parsed, list):
                    sentences = parsed
                elif isinstance(parsed, dict):
                    sentences = [parsed]
            except json.JSONDecodeError:
                # 整段文本，无分句信息
                sentences = [{"text": result_text, "begin_time": 0, "end_time": 0}]

        segments: list[ASRSegment] = []
        total_duration = 0.0

        for sent in sentences:
            text = str(sent.get("text", "")).strip()
            if not text:
                continue
            # 阿里云时间戳单位为毫秒
            start = float(sent.get("begin_time", 0)) / 1000.0
            end = float(sent.get("end_time", 0)) / 1000.0
            if end <= start:
                end = start + max(1.0, len(text) * 0.3)  # 估算
            confidence = float(sent.get("confidence", 0.85))
            confidence = max(0.0, min(1.0, confidence))
            segments.append(
                ASRSegment(start=start, end=end, text=text, confidence=confidence),
            )
            total_duration = max(total_duration, end)

        if not segments:
            raise AliyunASRError("识别结果为空，未提取到有效语音内容。", code="empty_result")

        return ASRResult(
            segments=segments,
            language=language,
            duration=total_duration,
            provider_name="aliyun_asr",
        )


def build_aliyun_asr_provider(
    *,
    access_key_id: str,
    access_key_secret: str,
    app_key: str,
) -> AliyunASRProvider:
    """工厂函数：构建 AliyunASRProvider 实例。"""
    return AliyunASRProvider(
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        app_key=app_key,
    )
