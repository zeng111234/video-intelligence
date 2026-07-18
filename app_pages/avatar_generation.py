from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import streamlit as st

from src.adapters.avatar import AvatarProviderError
from src.app_state import get_avatar_service, get_services
from src.models import (
    AvatarAssetKind,
    AvatarCapability,
    AvatarProviderStatus,
    AvatarSubmitRequest,
    AvatarTask,
    ProviderMode,
    TaskStatus,
    TranscriptRevision,
    TranscriptionTask,
)
from src.ui import render_page_header, render_task_badge


def _revision_label(task: TranscriptionTask, revision: TranscriptRevision) -> str:
    return (
        f"{task.updated_at:%m-%d %H:%M} · {task.media_name} · "
        f"第 {revision.revision_number} 版成稿"
    )


render_page_header(
    "数字人生成",
    "使用已确认成稿或授权文案创建真实异步数字人任务。",
    icon="smart_toy",
)

avatar_service = get_avatar_service()
_, _, transcription_service = get_services()

provider_error: str | None = None
try:
    capability = avatar_service.capabilities()
except AvatarProviderError as exc:
    provider_error = str(exc)
    capability = AvatarCapability(
        provider_name="company_internal_avatar",
        display_name="公司数字人服务",
        mode=ProviderMode.SANDBOX,
        enabled=False,
        permission_status="service_unreachable",
        missing_configuration=["公司数字人服务不可达"],
    )

assets = []
if capability.enabled:
    try:
        assets = avatar_service.list_assets()
    except AvatarProviderError as exc:
        provider_error = str(exc)

avatars = [item for item in assets if item.kind == AvatarAssetKind.AVATAR]
voices = [item for item in assets if item.kind == AvatarAssetKind.VOICE]
service_ready = capability.enabled and bool(avatars) and bool(voices)

if provider_error:
    st.error(provider_error, icon=":material/cloud_off:")
elif not capability.enabled:
    missing = "、".join(capability.missing_configuration) or "供应商配置"
    st.info(
        f"数字人服务尚未启用（缺少：{missing}）。页面不会提交任务或产生费用。",
        icon=":material/info:",
    )
elif not service_ready:
    st.warning("服务已连接，但没有同时可用且已授权的数字人形象和音色。")
else:
    st.success(
        f"已连接 {capability.display_name}，当前模式：{capability.mode.value}。",
        icon=":material/cloud_done:",
    )

approved_sources: list[tuple[TranscriptionTask, TranscriptRevision]] = []
for task in transcription_service.list_tasks():
    if (
        isinstance(task, TranscriptionTask)
        and not task.is_mock
        and task.status == TaskStatus.SUCCEEDED
    ):
        revision = transcription_service.get_approved_revision(task.task_id)
        if revision is not None:
            approved_sources.append((task, revision))

approved_by_task_id = {
    task.task_id: (task, revision) for task, revision in approved_sources
}
source_modes = ["已确认成稿", "手工输入"] if approved_sources else ["手工输入"]
if st.session_state.get("avatar_script_source_mode") not in source_modes:
    st.session_state["avatar_script_source_mode"] = source_modes[0]

with st.container(border=True):
    st.subheader("1. 选择文案")
    source_mode = st.segmented_control(
        "文案来源", source_modes, key="avatar_script_source_mode"
    )

    script_text = ""
    source_label = "临时手工文案"
    source_task_id = None
    source_revision_id = None
    if source_mode == "已确认成稿" and approved_sources:
        approved_task_ids = [task.task_id for task, _ in approved_sources]
        preselected_task_id = st.session_state.get("avatar_source_task_id")
        if preselected_task_id not in approved_task_ids:
            preselected_task_id = approved_task_ids[0]
        if (
            st.session_state.get("avatar_approved_task_selector")
            not in approved_task_ids
        ):
            st.session_state["avatar_approved_task_selector"] = preselected_task_id

        selected_task_id = st.selectbox(
            "选择已确认成稿",
            approved_task_ids,
            key="avatar_approved_task_selector",
            format_func=lambda task_id: _revision_label(*approved_by_task_id[task_id]),
        )
        selected_task, selected_revision = approved_by_task_id[selected_task_id]
        source_task_id = selected_task.task_id
        source_revision_id = selected_revision.revision_id
        st.session_state["avatar_source_task_id"] = source_task_id
        st.session_state["avatar_source_revision_id"] = source_revision_id
        script_text = "\n".join(
            segment.text for segment in selected_revision.corrected_segments
        )
        source_label = f"{selected_task.media_name} · 第 {selected_revision.revision_number} 版成稿"
        st.text_area(
            "已确认成稿预览",
            value=script_text,
            height=220,
            disabled=True,
            key=f"avatar_approved_script_{selected_revision.revision_id}",
        )
        st.caption(
            "这里只读取当前任务 approved_revision_id 指向的批准版本；"
            "草稿和原始识别结果不会进入本页。"
        )
    else:
        if not approved_sources:
            st.info("当前没有真实的已确认成稿，可临时手工输入或先完成转写校对。")
        script_text = st.text_area(
            "临时手工文案",
            placeholder="粘贴已经过事实、版权和品牌审核的口播文案……",
            height=220,
            key="avatar_manual_script",
            help="提交前仅保存在当前浏览器会话，不会写入数据库；提交后保存不可变文案快照。",
        )
        if st.button("返回视频音轨转文案", icon=":material/arrow_back:"):
            st.switch_page("app_pages/transcription.py")

    script_length = len(script_text.strip())
    st.caption(f"当前文案 {script_length}/{capability.max_script_chars} 个字符。")

avatar_options = {item.asset_id: item for item in avatars}
voice_options = {item.asset_id: item for item in voices}

with st.form("avatar_generation_form"):
    with st.container(border=True):
        st.subheader("2. 配置数字人")
        left, right = st.columns(2)
        with left:
            st.selectbox("驱动方式", ["文本驱动"], disabled=True)
            avatar_id = st.selectbox(
                "数字人形象",
                list(avatar_options),
                format_func=lambda item_id: avatar_options[item_id].name,
                disabled=not avatars,
                placeholder="等待服务返回已授权形象",
            )
            voice_id = st.selectbox(
                "音色",
                list(voice_options),
                format_func=lambda item_id: voice_options[item_id].name,
                disabled=not voices,
                placeholder="等待服务返回已授权音色",
            )
        with right:
            speech_rate = st.slider(
                "语速", min_value=0.8, max_value=1.2, value=1.0, step=0.05
            )
            aspect_ratio = st.selectbox(
                "画幅", capability.supported_aspect_ratios or ["9:16"]
            )
            resolution = st.selectbox("分辨率", ["1080x1920"])
            background = st.selectbox("背景", ["transparent", "brand", "solid"])

    with st.container(border=True):
        st.subheader("3. 授权与费用确认")
        st.warning(
            "已确认成稿不等于拥有数字人合成或发布权。提交前仍需完成事实、"
            "版权、肖像、声音以及目标平台 AI 内容标识审核。"
        )
        rights_holder = st.text_input(
            "权利主体", placeholder="例如：本公司或已取得授权的客户"
        )
        script_rights = st.checkbox("我确认拥有该文案的使用和改编权")
        avatar_rights = st.checkbox("我确认数字人形象来自官方预置或已取得肖像授权")
        voice_rights = st.checkbox("我确认音色来自官方预置或已取得声音授权")

        status_columns = st.columns(3)
        status_columns[0].metric(
            "供应商状态", "可用" if service_ready else "尚不可用", border=True
        )
        status_columns[1].metric(
            "预计费用",
            (
                f"¥{capability.estimated_cost_cny:.2f}"
                if capability.estimated_cost_cny is not None
                else "供应商未提供"
            ),
            border=True,
        )
        status_columns[2].metric(
            "预计生成时长",
            (
                f"约 {capability.estimated_seconds} 秒"
                if capability.estimated_seconds is not None
                else "供应商未提供"
            ),
            border=True,
        )

    with st.container(border=True):
        st.subheader("4. 提交")
        st.write(f"**文案来源：** {source_label}")
        confirmations_complete = all(
            [rights_holder.strip(), script_rights, avatar_rights, voice_rights]
        )
        submission_ready = all(
            [
                service_ready,
                script_text.strip(),
                script_length <= capability.max_script_chars,
                avatar_id,
                voice_id,
                confirmations_complete,
            ]
        )
        submitted = st.form_submit_button(
            "开始生成",
            type="primary",
            icon=":material/play_arrow:",
            disabled=not submission_ready,
            help=(
                "服务可用、文案合规且三项授权全部确认后才可提交。"
                if not submission_ready
                else "提交真实异步任务；供应商可能按其报价计费。"
            ),
        )

if submitted:
    request = AvatarSubmitRequest(
        script_text=script_text.strip(),
        source_task_id=source_task_id,
        source_revision_id=source_revision_id,
        avatar_id=avatar_id,
        voice_id=voice_id,
        speech_rate=speech_rate,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        background=background,
        rights_holder=rights_holder.strip(),
        script_rights_confirmed=script_rights,
        avatar_rights_confirmed=avatar_rights,
        voice_rights_confirmed=voice_rights,
        idempotency_key=f"avatar-{uuid4().hex}",
    )
    try:
        created_task = avatar_service.submit(
            request,
            avatar_name=avatar_options[avatar_id].name,
            voice_name=voice_options[voice_id].name,
        )
    except (AvatarProviderError, ValueError) as exc:
        st.error(str(exc), icon=":material/error:")
    else:
        st.session_state["active_avatar_task_id"] = created_task.task_id
        if created_task.provider_status == AvatarProviderStatus.OUTCOME_UNKNOWN:
            st.warning("提交结果未知，已保存幂等键；请使用下方按钮核对状态。")
        elif created_task.status == TaskStatus.FAILED:
            st.error(created_task.error_message or "数字人任务提交失败。")
        else:
            st.toast("数字人任务已提交", icon=":material/check_circle:")
        st.rerun()

avatar_tasks = [
    task for task in transcription_service.list_tasks() if isinstance(task, AvatarTask)
]
active_task_id = st.session_state.get("active_avatar_task_id")
active_task = next(
    (task for task in avatar_tasks if task.task_id == active_task_id),
    avatar_tasks[0] if avatar_tasks else None,
)

if active_task:
    with st.container(border=True):
        with st.container(
            horizontal=True,
            horizontal_alignment="distribute",
            vertical_alignment="center",
        ):
            st.subheader("最近数字人任务")
            render_task_badge(active_task.status)
        st.caption(
            f"任务 {active_task.task_id} · {active_task.avatar_name} · "
            f"{active_task.voice_name}"
        )
        st.progress(active_task.progress, text=active_task.stage)
        if active_task.error_message:
            st.error(active_task.error_message, icon=":material/error:")

        can_refresh = active_task.provider_status in {
            AvatarProviderStatus.QUEUED,
            AvatarProviderStatus.RUNNING,
            AvatarProviderStatus.OUTCOME_UNKNOWN,
        }
        action_columns = st.columns(2)
        if action_columns[0].button(
            "刷新任务状态",
            icon=":material/refresh:",
            disabled=not can_refresh,
        ):
            avatar_service.refresh_task(active_task.task_id)
            st.rerun()

        can_download = (
            active_task.provider_status == AvatarProviderStatus.SUCCEEDED
            and not active_task.result_path
        )
        if action_columns[1].button(
            "保存生成视频",
            icon=":material/download:",
            disabled=not can_download,
        ):
            try:
                avatar_service.download_result(active_task.task_id)
            except (AvatarProviderError, RuntimeError, ValueError) as exc:
                st.error(str(exc), icon=":material/error:")
            else:
                st.toast("生成视频已验证并保存", icon=":material/check_circle:")
                st.rerun()

        if active_task.result_path and Path(active_task.result_path).is_file():
            st.video(active_task.result_path)
            st.caption(
                f"本地结果：{active_task.result_path} · "
                f"{(active_task.result_size_bytes or 0) / 1024 / 1024:.1f} MB"
            )

with st.expander("接入架构与边界", icon=":material/account_tree:"):
    st.markdown(
        "`确认成稿 / 手工文案 → AvatarService → InternalAvatarProvider → "
        "公司 PHP 内部 API → 真实供应商 → 异步任务 → 已验证视频结果`"
    )
    st.write(
        "PHP 内部 API 使用独立服务令牌，不复用旧会员登录、积分或付费逻辑。"
        "未配置供应商、真实资产或授权时，本页保持禁用并说明原因。"
    )
