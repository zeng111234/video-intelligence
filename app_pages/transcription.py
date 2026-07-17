from __future__ import annotations

import pandas as pd
import streamlit as st

from src.app_state import get_services, selected_candidate_id
from src.models import TranscriptSegment, TranscriptionTask
from src.ui import render_page_header, render_task_badge

render_page_header(
    "音视频转文案",
    "只处理用户上传、客户自有账号或明确授权的媒体；当前处理结果为 Mock 演示。",
    icon="transcribe",
)

candidate_service, _, transcription_service = get_services()
candidate = candidate_service.get(selected_candidate_id())
if candidate:
    with st.container(border=True):
        st.markdown("**已从热点候选榜带入**")
        st.write(f"### {candidate.title}")
        st.caption(
            f"{candidate.author_name} · {candidate.category} · 仅关联元数据，不自动下载原视频"
        )
        st.link_button(
            "查看视频来源",
            str(candidate.source_url),
            icon=":material/open_in_new:",
        )

left, right = st.columns([1, 1.45], vertical_alignment="top")
with left:
    with st.container(border=True):
        st.subheader("创建演示任务")
        if candidate:
            st.info(
                "请上传你有权处理的该视频媒体文件；系统不会自动抓取或下载抖音原视频。",
                icon=":material/info:",
            )
        use_demo_media = st.toggle("使用内置演示媒体", value=True)
        uploaded_file = st.file_uploader(
            "上传授权媒体",
            type=["mp4", "mov", "mp3", "wav"],
            disabled=use_demo_media,
            help="框架版不会保存或处理上传内容。",
        )
        rights_confirmed = st.checkbox(
            "我确认拥有该媒体的处理权，并同意用于本次私有转写"
        )
        media_name = (
            "授权口播演示.mp4"
            if use_demo_media
            else (uploaded_file.name if uploaded_file else "")
        )
        media_type = (
            "video/mp4"
            if use_demo_media
            else (uploaded_file.type if uploaded_file else "application/octet-stream")
        )
        can_create = bool(media_name and rights_confirmed)
        if st.button(
            "生成 Mock 转写",
            type="primary",
            icon=":material/play_arrow:",
            disabled=not can_create,
        ):
            task = transcription_service.create_mock_task(
                media_name=media_name,
                media_type=media_type,
                rights_confirmed=rights_confirmed,
                candidate_id=selected_candidate_id(),
            )
            st.session_state.active_transcription_task_id = task.task_id
            st.toast("Mock 转写任务已完成", icon=":material/check_circle:")
            st.rerun()
        if not rights_confirmed:
            st.caption("确认媒体授权后才可创建任务。")

    with st.container(border=True):
        st.subheader("处理阶段")
        active = transcription_service.repository.get_task(
            st.session_state.active_transcription_task_id
        )
        progress = active.progress if isinstance(active, TranscriptionTask) else 0
        st.progress(progress, text=f"Mock 进度 {progress}%")
        for label in ["媒体探测", "音频提取", "语音转写", "低置信度标记", "导出结果"]:
            st.markdown(
                f":green-badge[完成] {label}"
                if progress == 100
                else f":blue-badge[演示] {label}"
            )

with right:
    task = transcription_service.repository.get_task(
        st.session_state.active_transcription_task_id
    )
    with st.container(border=True):
        with st.container(
            horizontal=True,
            horizontal_alignment="distribute",
            vertical_alignment="center",
        ):
            st.subheader("转写与校对")
            if isinstance(task, TranscriptionTask):
                render_task_badge(task.status)
        if not isinstance(task, TranscriptionTask) or not task.segments:
            st.info(
                "创建演示任务后，这里会显示带时间戳的转写结果。", icon=":material/info:"
            )
        else:
            st.caption("低于 0.75 的片段已标记待复核；可直接修改文案列。")
            editor_data = pd.DataFrame(
                [
                    {
                        "start": segment.start,
                        "end": segment.end,
                        "文案": segment.text,
                        "置信度": segment.confidence,
                        "待复核": segment.needs_review,
                    }
                    for segment in task.segments
                ]
            )
            edited = st.data_editor(
                editor_data,
                hide_index=True,
                disabled=["start", "end", "置信度", "待复核"],
                key=f"transcript_editor_{task.task_id}",
                column_config={
                    "start": st.column_config.NumberColumn("开始（秒）", format="%.1f"),
                    "end": st.column_config.NumberColumn("结束（秒）", format="%.1f"),
                    "文案": st.column_config.TextColumn(width="large"),
                    "置信度": st.column_config.ProgressColumn(
                        min_value=0, max_value=1, format="%.0%%"
                    ),
                    "待复核": st.column_config.CheckboxColumn(),
                },
            )
            corrected_segments = [
                TranscriptSegment(
                    start=float(row["start"]),
                    end=float(row["end"]),
                    text=str(row["文案"]),
                    confidence=float(row["置信度"]),
                    needs_review=bool(row["待复核"]),
                )
                for row in edited.to_dict(orient="records")
            ]
            with st.container(horizontal=True):
                st.download_button(
                    "下载 TXT",
                    transcription_service.export_txt(corrected_segments),
                    file_name="transcript.txt",
                    mime="text/plain",
                    icon=":material/download:",
                )
                st.download_button(
                    "下载 JSON",
                    transcription_service.export_json(corrected_segments),
                    file_name="transcript.json",
                    mime="application/json",
                    icon=":material/download:",
                )
                st.download_button(
                    "下载 SRT",
                    transcription_service.export_srt(corrected_segments),
                    file_name="subtitles.srt",
                    mime="application/x-subrip",
                    icon=":material/download:",
                )
