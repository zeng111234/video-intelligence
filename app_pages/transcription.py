from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from src.app_state import get_services, selected_candidate_id
from src.models import (
    TaskStatus,
    TranscriptSegment,
    TranscriptionTask,
)
from src.platforms import platform_label
from src.resources import runtime_capabilities
from src.services.transcription import TranscriptionError
from src.services.video_source import VideoSourceError, fetch_authorized_video
from src.ui import render_page_header, render_task_badge


def _task_label(task: TranscriptionTask) -> str:
    status = {
        TaskStatus.RUNNING: "处理中",
        TaskStatus.SUCCEEDED: "待校对/已完成",
        TaskStatus.FAILED: "失败",
    }.get(task.status, str(task.status))
    return f"{task.updated_at:%m-%d %H:%M} · {task.media_name} · {status}"


def _build_segments(
    rows: list[dict[str, object]],
) -> tuple[list[TranscriptSegment], str | None]:
    corrected: list[TranscriptSegment] = []
    previous_end = 0.0
    for index, row in enumerate(rows, start=1):
        raw_text = row.get("文案")
        text = "" if pd.isna(raw_text) else str(raw_text).strip()
        if not text:
            return [], f"第 {index} 段文案为空，请补充后再保存。"
        try:
            start = float(row["start"])
            end = float(row["end"])
            confidence = float(row["confidence"])
        except (KeyError, TypeError, ValueError):
            return [], f"第 {index} 段数据无效，请刷新后重新校对。"
        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end <= start
            or start < previous_end
        ):
            return [], f"第 {index} 段时间无效，请刷新后重新校对。"
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            return [], f"第 {index} 段置信度无效，请刷新后重新校对。"
        corrected.append(
            TranscriptSegment(
                start=start,
                end=end,
                text=text,
                confidence=confidence,
                needs_review=bool(row["needs_review"]),
                reviewed=bool(row["已复核"]),
            )
        )
        previous_end = end
    return corrected, None


render_page_header(
    "爆火视频音轨转文案",
    "直接上传视频或填写视频直链；系统只提取音轨转写，不做抽帧或画面分析。",
    icon="transcribe",
)

candidate_service, _, transcription_service = get_services()
candidate = candidate_service.get(selected_candidate_id())
capabilities = runtime_capabilities()
candidate_id = candidate.video_id if candidate else None
scope_id = candidate_id or "standalone"
failure_key = f"transcription_attempt_failed_{scope_id}"
selector_key = f"transcription_task_selector_{scope_id}"

with st.container(border=True):
    st.subheader("1. 候选关联（可选）")
    if candidate:
        st.markdown(f"**{candidate.title}**")
        st.caption(
            f"{platform_label(candidate.platform)} · {candidate.author_name} · "
            f"{candidate.category}"
        )
        with st.container(horizontal=True):
            if candidate.source_url:
                st.link_button(
                    "查看候选来源",
                    str(candidate.source_url),
                    icon=":material/open_in_new:",
                )
            if st.button("重新选择候选", icon=":material/arrow_back:"):
                st.switch_page("app_pages/candidates.py")
        if candidate.feed_id or candidate.finder_user_name:
            st.caption(
                f"视频号追溯字段：feedId={candidate.feed_id or '—'}；"
                f"finderUserName={candidate.finder_user_name or '—'}"
            )
    else:
        st.caption("当前为独立体验模式，无需等待第一页接口或先确认爆火候选。")
        if st.button("也可以去选择候选", icon=":material/arrow_back:"):
            st.switch_page("app_pages/candidates.py")

with st.container(border=True):
    st.subheader("2. 提供有权处理的视频")
    st.caption(
        "仅支持 MP4、MOV；系统不会分析画面，只提取视频中的音轨。"
        "单文件不超过 50MB、15 分钟。"
    )
    input_mode = st.segmented_control(
        "输入方式",
        ["上传视频", "视频直链"],
        default="上传视频",
    )
    uploaded_file = None
    direct_url = ""
    if input_mode == "上传视频":
        uploaded_file = st.file_uploader(
            "上传授权视频",
            type=["mp4", "mov"],
            help="只处理本次主动上传的视频，原视频与临时音频处理后立即清理。",
        )
        if uploaded_file is not None:
            st.video(uploaded_file.getvalue(), format=uploaded_file.type or None)
    else:
        direct_url = st.text_input(
            "视频直链",
            placeholder="https://example.com/authorized-video.mp4",
            help=(
                "只接受直接返回 MP4/MOV 文件的 HTTPS 公网地址；"
                "抖音、小红书、视频号等平台分享页暂不支持。"
            ),
        ).strip()
        st.caption("链接只在点击开始后读取，不保存链接，也不使用平台下载器。")
    quality_mode = st.segmented_control(
        "转写模式",
        ["准确率优先", "快速预览"],
        default="准确率优先",
        help="准确率优先使用更大的本地模型；快速预览保留原来的 base 模型。",
    )
    model_name = "large-v3-turbo" if quality_mode == "准确率优先" else "base"
    if quality_mode == "准确率优先":
        st.caption("使用 large-v3-turbo；首次运行需要下载模型，速度会慢于快速预览。")
        st.caption("本地热词实验会增加漏句风险，当前不启用词汇改写或偏置。")
    rights_holder = st.text_input("媒体权利主体", placeholder="例如：本公司自有账号")
    rights_confirmed = st.checkbox(
        "我确认拥有该媒体的处理权，并同意仅用于本次私有文案转写"
    )
    missing_conditions = []
    if input_mode == "上传视频" and uploaded_file is None:
        missing_conditions.append("上传视频")
    if input_mode == "视频直链" and not direct_url:
        missing_conditions.append("填写视频直链")
    if not rights_holder.strip():
        missing_conditions.append("填写媒体权利主体")
    if not rights_confirmed:
        missing_conditions.append("确认处理权")
    if missing_conditions:
        st.caption("开始前还需：" + "、".join(missing_conditions) + "。")
    if not capabilities["ffmpeg"]:
        st.error("本机缺少 FFmpeg/FFprobe，请联系技术人员安装后再试。")
    if not capabilities["asr"]:
        st.error("本机尚未安装 faster-whisper，暂时无法执行真实本地转写。")
    submitted = st.button(
        "提取音轨并转成文案",
        type="primary",
        icon=":material/play_arrow:",
        disabled=bool(
            missing_conditions or not capabilities["ffmpeg"] or not capabilities["asr"]
        ),
    )

with st.container(border=True):
    st.subheader("3. 查看真实处理状态")
    progress_rendered = False
    if submitted:
        has_video_input = uploaded_file is not None or bool(direct_url)
        if not has_video_input or not rights_holder.strip() or not rights_confirmed:
            st.error("请完成视频输入、权利主体和处理权确认后再开始。")
        else:
            st.session_state["active_transcription_task_id"] = None
            st.session_state[failure_key] = False
            locked_task_id: list[str] = []
            status_box = st.status("正在创建本地转写任务……", expanded=True)
            progress_bar = st.progress(0, text="等待处理")
            progress_rendered = True

            def on_progress(task: TranscriptionTask) -> None:
                if not locked_task_id:
                    locked_task_id.append(task.task_id)
                    st.session_state["active_transcription_task_id"] = task.task_id
                    st.session_state[selector_key] = task.task_id
                progress_bar.progress(task.progress, text=task.stage)
                state = "running"
                if task.status == TaskStatus.SUCCEEDED:
                    state = "complete"
                elif task.status == TaskStatus.FAILED:
                    state = "error"
                status_box.update(label=task.stage, state=state)

            try:
                if input_mode == "视频直链":
                    status_box.update(label="正在读取视频直链……", state="running")
                    progress_bar.progress(5, text="读取视频直链")
                    direct_video = fetch_authorized_video(direct_url)
                    media_name = direct_video.name
                    media_type = direct_video.media_type
                    media_bytes = direct_video.content
                else:
                    if uploaded_file is None:
                        raise VideoSourceError("请先上传视频文件。")
                    media_name = uploaded_file.name
                    media_type = uploaded_file.type or "application/octet-stream"
                    media_bytes = uploaded_file.getvalue()
                task = transcription_service.create_task(
                    media_name=media_name,
                    media_type=media_type,
                    media_bytes=media_bytes,
                    rights_confirmed=rights_confirmed,
                    rights_holder=rights_holder,
                    candidate_id=candidate_id,
                    model_name=model_name,
                    on_progress=on_progress,
                )
                st.session_state["active_transcription_task_id"] = task.task_id
                st.session_state[failure_key] = False
                st.success("识别完成，请在下一步校对并确认成稿。")
            except VideoSourceError as exc:
                st.session_state[failure_key] = True
                status_box.update(label="视频读取失败", state="error")
                st.error(exc.user_message, icon=":material/error:")
            except TranscriptionError as exc:
                st.session_state[failure_key] = True
                st.error(exc.user_message, icon=":material/error:")
            except Exception:
                st.session_state[failure_key] = True
                st.error(
                    "本地转写处理失败，请重新上传后再试。",
                    icon=":material/error:",
                )

    active_id = st.session_state.get("active_transcription_task_id")
    active_status = (
        transcription_service.repository.get_task(active_id) if active_id else None
    )
    if (
        isinstance(active_status, TranscriptionTask)
        and active_status.candidate_id != candidate_id
    ):
        active_status = None
        st.session_state["active_transcription_task_id"] = None
    if not progress_rendered and isinstance(active_status, TranscriptionTask):
        st.progress(active_status.progress, text=active_status.stage)
        render_task_badge(active_status.status)
        if active_status.error_message:
            st.error("处理失败，媒体已清理，请重新上传。")
    elif not progress_rendered:
        st.caption("提交授权视频后，这里会同步显示视频检查、音轨提取和语音识别阶段。")

with st.container(border=True):
    st.subheader("4. 校对与导出")
    if st.session_state.get(failure_key):
        st.warning("本次处理失败且媒体已清理，请重新上传；不会自动串用旧任务。")
        st.stop()

    real_tasks = [
        task
        for task in transcription_service.repository.list_tasks()
        if isinstance(task, TranscriptionTask)
        and not task.is_mock
        and task.candidate_id == candidate_id
    ]
    if not real_tasks:
        empty_label = "当前候选" if candidate else "独立体验"
        st.info(f"{empty_label}还没有真实转写任务。")
        st.stop()

    active_id = st.session_state.get("active_transcription_task_id")
    task_ids = [task.task_id for task in real_tasks]
    if st.session_state.get(selector_key) not in task_ids:
        st.session_state[selector_key] = (
            active_id if active_id in task_ids else task_ids[0]
        )
    selected_task_id = st.selectbox(
        "继续校对最近任务",
        task_ids,
        format_func=lambda task_id: _task_label(
            next(task for task in real_tasks if task.task_id == task_id)
        ),
        key=selector_key,
    )
    st.session_state["active_transcription_task_id"] = selected_task_id
    active = transcription_service.repository.get_task(selected_task_id)
    if not isinstance(active, TranscriptionTask) or not active.segments:
        st.info("该任务没有可校对的转写片段；失败任务需重新上传视频。")
        st.stop()

    revisions = transcription_service.repository.list_transcript_revisions(
        active.task_id
    )
    latest = revisions[-1] if revisions else None
    base_segments = latest.corrected_segments if latest else active.segments
    editor_data = pd.DataFrame(
        [
            {
                "start": segment.start,
                "end": segment.end,
                "文案": segment.text,
                "confidence": segment.confidence,
                "needs_review": segment.needs_review or segment.confidence < 0.75,
                "已复核": segment.reviewed,
            }
            for segment in base_segments
        ]
    )
    edited = st.data_editor(
        editor_data,
        hide_index=True,
        width="stretch",
        disabled=["start", "end", "confidence", "needs_review"],
        key=f"transcript_editor_{active.task_id}_{len(revisions)}",
        column_config={
            "start": st.column_config.NumberColumn("开始（秒）", format="%.1f"),
            "end": st.column_config.NumberColumn("结束（秒）", format="%.1f"),
            "文案": st.column_config.TextColumn("文案", width="large"),
            "confidence": st.column_config.ProgressColumn(
                "置信度", min_value=0, max_value=1, format="%.0%%"
            ),
            "needs_review": st.column_config.CheckboxColumn("待复核"),
            "已复核": st.column_config.CheckboxColumn(
                "已复核", help="低置信度片段确认无误或完成修改后勾选。"
            ),
        },
    )
    corrected, segment_error = _build_segments(edited.to_dict(orient="records"))
    if segment_error:
        st.warning(segment_error)
    pending_review_count = sum(
        segment.needs_review and not segment.reviewed for segment in corrected
    )
    version_label = f"第 {latest.revision_number} 版" if latest else "原始识别"
    duration_label = (
        f"{active.duration_seconds:.1f} 秒"
        if active.duration_seconds is not None
        else "未知"
    )
    st.caption(
        f"片段 {len(base_segments)} · 待复核 {pending_review_count} · "
        f"语言 {active.language or '未知'} · 模型 {active.model_name or '未知'} · "
        f"时长 {duration_label} · {version_label}"
    )

    reviewer_key = f"transcript_reviewer_{active.task_id}"
    st.session_state.setdefault(
        reviewer_key,
        (latest.reviewer if latest else None) or active.rights_holder or "",
    )
    reviewer = st.text_input("校对人", key=reviewer_key)
    if not reviewer.strip():
        st.caption("请填写校对人后再保存或确认成稿。")
    draft_disabled = bool(segment_error or not reviewer.strip())
    with st.container(horizontal=True):
        save_draft = st.button(
            "保存校对草稿",
            icon=":material/save:",
            disabled=draft_disabled,
        )
        approve = st.button(
            "确认成稿",
            type="primary",
            icon=":material/verified:",
            disabled=bool(draft_disabled or pending_review_count > 0),
        )
    if save_draft or approve:
        try:
            transcription_service.save_revision(
                active.task_id,
                corrected,
                reviewer=reviewer,
                approve=approve,
            )
            if approve:
                st.success("成稿已确认，现在可以导出。")
            else:
                st.toast("校对草稿已追加保存", icon=":material/check:")
            st.rerun()
        except TranscriptionError as exc:
            st.error(exc.user_message, icon=":material/error:")
        except Exception:
            st.error("校对版本保存失败，请刷新后重试。", icon=":material/error:")

    approved = transcription_service.get_approved_revision(active.task_id)
    if approved:
        st.success(f"已确认第 {approved.revision_number} 版成稿，可下载以下文件。")
        with st.container(horizontal=True):
            st.download_button(
                "下载 TXT",
                transcription_service.export_txt(approved.corrected_segments),
                file_name="文案.txt",
                mime="text/plain",
            )
            st.download_button(
                "下载 JSON",
                transcription_service.export_json(approved.corrected_segments),
                file_name="文案.json",
                mime="application/json",
            )
            st.download_button(
                "下载 SRT",
                transcription_service.export_srt(approved.corrected_segments),
                file_name="字幕.srt",
                mime="application/x-subrip",
            )
    else:
        st.caption("草稿不能导出；只有当前任务 approved_revision_id 指向的成稿可下载。")
