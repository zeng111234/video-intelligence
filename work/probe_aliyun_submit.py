import os
import sys
import traceback
sys.path.insert(0, 'C:/Users/zeng/Desktop/video')

# 不打印 key, 只显示前 6 位做诊断
from project.backend.app.core.config import ALIBABA_CLOUD_ACCESS_KEY_ID
key_prefix = ALIBABA_CLOUD_ACCESS_KEY_ID[:6] + "..." if ALIBABA_CLOUD_ACCESS_KEY_ID else "<none>"
print(f"access_key_prefix = {key_prefix}")

from src.services.cloud_transcription import AliyunFunASRRuntime, ASRAuthorizationStore
from project.backend.app.core.config import RUNTIME_ROOT
auth_store = ASRAuthorizationStore(os.path.join(str(RUNTIME_ROOT), "data", "production", "asr_authorization.json"))
runtime = AliyunFunASRRuntime(authorization_store=auth_store)

# 探针 1: capability
try:
    cap = runtime.capability()
    print(f"capability = {cap}")
except Exception as e:
    print(f"capability_FAIL = {type(e).__name__}: {e}")
    traceback.print_exc()

# 探针 2: 直接调 ensure_authorized 看配额
try:
    auth = runtime.ensure_authorized(duration_seconds=10)
    print(f"ensure_authorized OK: {auth}")
except Exception as e:
    print(f"ensure_authorized_FAIL = {type(e).__name__}: {e}")
    traceback.print_exc()
