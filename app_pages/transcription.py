from __future__ import annotations

import pandas as pd
import streamlit as st

from src.app_state import get_services, selected_candidate_id
from src.models import TranscriptSegment, TranscriptStatus, TranscriptionTask
from src.platforms import platform_label
from src.resources import runtime_capabilities
from src.ui import render_page_header, render_task_badge

render_page_header(
    "音视频转文案",
    "上传你有权处理的媒体，系统将在本机提取音频、识别文字并支持校对导出。",
    icon="transcribe",
)

candidate_service, _, transcription_service = get_services()
candidate = candidate_service.get(selected_candidate_id())
capabilities = runtime_capabilities()

if candidate:
    with st.container(border=True):
        st.markdown("**当前已选热门视频**")
        st.write(f"### {candidate.title}")
        st.caption(
            f"{platform_label(candidate.platform)} · {candidate.author_name} · {candidate.category}"
        )
        with st.container(horizontal=True):
            if candidate.source_url:
                st.link_button(
                    "查看视频来源",
                    str(candidate.source_url),
                    icon=":material/open_in_new:",
                )
            if st.button("重新选择视频", icon=":material/arrow_back:"):
                st.switch_page("app_pages/candidates.py")
        if candidate.feed_id or candidate.finder_user_name:
            st.caption(
                f"视频号追溯字段：feedId={candidate.feed_id or '—'}；"
                f"finderUserName={candidate.finder_user_name or '—'}"
            )
else:
    st.warning("请先在爆火视频检索页单选一个视频。", icon=":material/info:")
    if st.button("返回选择热门视频", icon=":material/arrow_back:"):
        st.switch_page("app_pages/candidates.py")

active_id = st.session_state.get("active_transcription_task_id")
active = transcription_service.repository.get_task(active_id) if active_id else None
if (
    isinstance(active, TranscriptionTask)
    and candidate
    and active.candidate_id
    not in {
        None,
        candidate.video_id,
    }
):
    active = None
    st.session_state["active_transcription_task_id"] = None

left, right = st.columns([1, 1.45], vertical_alignment="top")
with left:
    with st.container(border=True):
        st.subheader("上传并识别")
        uploaded_file = st.file_uploader(
            "上传授权媒体",
            type=["mp4", "mov", "m4a", "mp3", "wav"],
            help="单文件不超过50MB、时长不超过15分钟；原始文件处理后立即删除。",
        )
        rights_holder = st.text_input(
            "媒体权利主体", placeholder="例如：本公司自有账号"
        )
        rights_confirmed = st.checkbox(
            "我确认拥有该媒体的处理权，并同意仅用于本次私有文案转写"
        )
        can_create = bool(
            candidate
            and uploaded_file
            and rights_holder.strip()
            and rights_confirmed
            and capabilities["ffmpeg"]
            and capabilities["asr"]
        )
        if not capabilities["ffmpeg"]:
            st.error("本机缺少音频处理组件，请联系技术人员安装。")
        if not capabilities["asr"]:
            st.error("本机尚未安装语音识别组件，请联系技术人员安装。")
        if st.button(
            "开始转成文案",
            type="primary",
            icon=":material/play_arrow:",
            disabled=not can_create,
        ):
            try:
                with st.spinner("正在本机处理媒体，首次使用可能需要下载识别模型……"):
                    task = transcription_service.create_task(
                        media_name=uploaded_file.name,
                        media_type=uploaded_file.type or "application/octet-stream",
                        media_bytes=uploaded_file.getvalue(),
                        rights_confirmed=rights_confirmed,
                        rights_holder=rights_holder,
                        candidate_id=candidate.video_id,
                    )
                st.session_state["active_transcription_task_id"] = task.task_id
                st.success("识别完成，请校对后确认成稿。")
                st.rerun()
            except Exception as exc:
                st.error(str(exc), icon=":material/error:")

    with st.container(border=True):
        st.subheader("处理状态")
        if isinstance(active, TranscriptionTask):
            st.progress(active.progress, text=active.stage)
            render_task_badge(active.status)
            if active.error_message:
                st.error(active.error_message)
        else:
            st.caption("上传文件并确认授权后开始处理。")

with right:
    with st.container(border=True):
        st.subheader("校对与成稿")
        if not isinstance(active, TranscriptionTask) or not active.segments:
            st.info("真实识别完成后，这里会显示带时间戳的文案。")
        else:
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
                        "置信度": segment.confidence,
                        "待复核": segment.needs_review,
                    }
                    for segment in base_segments
                ]
            )
            edited = st.data_editor(
                editor_data,
                hide_index=True,
                disabled=["start", "end", "置信度", "待复核"],
                key=f"transcript_editor_{active.task_id}_{len(revisions)}",
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
            corrected = [
                TranscriptSegment(
                    start=float(row["start"]),
                    end=float(row["end"]),
                    text=str(row["文案"]).strip(),
                    confidence=float(row["置信度"]),
                    needs_review=bool(row["待复核"]),
                )
                for row in edited.to_dict(orient="records")
            ]
            reviewer = st.text_input("校对人", value=rights_holder or "内容审核员")
            with st.container(horizontal=True):
                if st.button("保存校对", icon=":material/save:"):
                    transcription_service.save_revision(
                        active.task_id, corrected, reviewer=reviewer
                    )
                    st.toast("校对版本已保存", icon=":material/check:")
                    st.rerun()
                if st.button("确认成稿", type="primary", icon=":material/verified:"):
                    transcription_service.save_revision(
                        active.task_id,
                        corrected,
                        reviewer=reviewer,
                        approve=True,
                    )
                    st.success("成稿已确认，现在可以导出。")
                    st.rerun()

            approved = next(
                (
                    revision
                    for revision in reversed(revisions)
                    if revision.status == TranscriptStatus.APPROVED
                ),
                None,
            )
            if approved:
                segments = approved.corrected_segments
                st.success(
                    f"已确认第 {approved.revision_number} 版成稿，数字人系统可使用以下文件。"
                )
                with st.container(horizontal=True):
                    st.download_button(
                        "下载纯文案",
                        transcription_service.export_txt(segments),
                        file_name="文案.txt",
                        mime="text/plain",
                    )
                    st.download_button(
                        "下载结构化文案",
                        transcription_service.export_json(segments),
                        file_name="文案.json",
                        mime="application/json",
                    )
                    st.download_button(
                        "下载字幕",
                        transcription_service.export_srt(segments),
                        file_name="字幕.srt",
                        mime="application/x-subrip",
                    )
            else:
                st.caption("保存校对后仍不能导出；请先点击“确认成稿”。")
