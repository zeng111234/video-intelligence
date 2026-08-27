import os, sys, traceback
sys.path.insert(0, 'C:/Users/zeng/Desktop/video')
from src.services.cloud_transcription import AliyunFunASRRuntime, ASRAuthorizationStore
from project.backend.app.core.config import RUNTIME_ROOT

auth_store = ASRAuthorizationStore(os.path.join(str(RUNTIME_ROOT), "data", "production", "asr_authorization.json"))
runtime = AliyunFunASRRuntime(authorization_store=auth_store)

# 看 runtime 的 submit 方法签名
import inspect
print("submit signature:", inspect.signature(runtime.submit))
print("upload signature:", inspect.signature(runtime.upload))
