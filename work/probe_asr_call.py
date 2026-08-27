import os
import sys
import traceback
sys.path.insert(0, 'C:/Users/zeng/Desktop/video')

from project.backend.app.core.config import ASR_MODE
from src.services.transcription import TranscriptionService
from src.repositories.sqlite import SQLiteRepository

repo = SQLiteRepository(os.path.abspath('data/video_intelligence.db'))
service = TranscriptionService(repo, cloud_runtime=None)  # 默认 None
print(f"service.cloud_runtime = {service.cloud_runtime}")

# 复刻 deps.py 完整逻辑: ASR_MODE=cloud + desktop_control_plane=False -> AliyunFunASRRuntime + transcription_media 目录
from project.backend.app.core.config import RUNTIME_ROOT
if ASR_MODE.value == "cloud":
    from src.services.cloud_transcription import AliyunFunASRRuntime, ASRAuthorizationStore
    runtime_root = str(RUNTIME_ROOT)
    auth_store = ASRAuthorizationStore(os.path.join(runtime_root, "data", "production", "asr_authorization.json"))
    runtime = AliyunFunASRRuntime(authorization_store=auth_store)
    service.cloud_runtime = runtime
    service.cloud_storage_directory = RUNTIME_ROOT / "data" / "production" / "transcription_media"
    print(f"service.cloud_runtime         = {type(runtime).__name__}  (forced)")
    print(f"service.cloud_storage_dir     = {service.cloud_storage_directory}")
    print(f"storage_dir exists            = {service.cloud_storage_directory.exists()}")

# 1KB 假 mp4, 故意触发真实错误
fake_mp4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 200

try:
    task = service.create_task(
        media_name="probe.mp4",
        media_bytes=fake_mp4,
        media_type="video/mp4",
        rights_holder="诊断",
        rights_confirmed=True,
        source_kind="manual_upload",
    )
    print(f"task_id = {task.task_id}")
    print(f"status   = {task.status.value}")
    print(f"stage    = {task.stage}")
    print(f"error    = {task.error}")
except Exception as e:
    print(f"EXC: {type(e).__name__}: {e}")
    traceback.print_exc()
