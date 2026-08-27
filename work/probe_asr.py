import os
import sys
sys.path.insert(0, 'C:/Users/zeng/Desktop/video')

from project.backend.app.core.config import ASR_MODE, ALIBABA_CLOUD_ACCESS_KEY_ID
from project.backend.app.services.control_plane_client import control_plane_enabled
from src.repositories.sqlite import SQLiteRepository

# 复用 deps.py 的 cloud_runtime 决定逻辑
desktop_control_plane = False
if os.getenv("VIDEOINSIGHT_DESKTOP_CLIENT", "").strip().casefold() in {"1","true","yes","on"}:
    desktop_control_plane = control_plane_enabled()

print(f"ASR_MODE        = {ASR_MODE.value}")
print(f"ALIBABA_KEY_set = {bool(ALIBABA_CLOUD_ACCESS_KEY_ID)}")
print(f"control_plane   = {desktop_control_plane}")

if desktop_control_plane:
    from project.backend.app.services.remote_asr import RemoteAliyunASRRuntime
    runtime = RemoteAliyunASRRuntime()
    print(f"runtime_type    = RemoteAliyunASRRuntime")
    print(f"runtime.endpoint = {getattr(runtime, 'endpoint', '<unknown>')}")
elif ASR_MODE.value == "cloud":
    from src.services.cloud_transcription import AliyunFunASRRuntime
    from src.services.cloud_transcription import ASRAuthorizationStore
    runtime_root = os.path.abspath('.')
    auth_store = ASRAuthorizationStore(os.path.join(runtime_root, "data", "production", "asr_authorization.json"))
    runtime = AliyunFunASRRuntime(authorization_store=auth_store)
    print(f"runtime_type    = AliyunFunASRRuntime")
    # 探针：看 capability() 是不是抛错
    try:
        cap = runtime.capability()
        print(f"capability_ok   = {cap}")
    except Exception as e:
        print(f"capability_FAIL = {type(e).__name__}: {e}")
else:
    print("runtime_type    = NONE (would fall back to local faster-whisper)")

# 探针：调一次 capability 看连通性
if ASR_MODE.value == "cloud" and not desktop_control_plane:
    try:
        # 检查是否还有未结算的 quota
        if hasattr(runtime, "ensure_authorized"):
            print(f"ensure_authorized_present = yes")
        else:
            print(f"ensure_authorized_present = no")
    except Exception as e:
        print(f"probe_FAIL: {type(e).__name__}: {e}")
