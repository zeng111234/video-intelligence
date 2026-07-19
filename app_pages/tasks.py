from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from src.app_state import get_services
from src.models import AvatarTask, TaskKind, TaskStatus, AvatarProviderStatus
from src.ui import render_page_header, render_task_badge

render_page_header(
    "任务记录",
    "查看搜索、转写与数字人任务的状态、耗时、输出和明确错误。",
    icon="history",
)

_, _, transcription_service = get_services()
tasks = transcription_service.list_tasks()

with st.container(horizontal=True):
    st.metric("全部任务", len(tasks), border=True)
    st.metric(
        "处理中", sum(task.status == TaskStatus.RUNNING for task in tasks), border=True
    )
    st.metric(
        "已完成",
        sum(task.status == TaskStatus.SUCCEEDED for task in tasks),
        border=True,
    )
    st.metric(
        "失败", sum(task.status == TaskStatus.FAILED for task in tasks), border=True
    )

status_filter = st.pills(
    "状态筛选",
    ["全部", "处理中", "已完成", "失败"],
    default="全部",
)
status_map = {
    "处理中": TaskStatus.RUNNING,
    "已完成": TaskStatus.SUCCEEDED,
    "失败": TaskStatus.FAILED,
}
filtered_tasks = (
    tasks
    if status_filter == "全部"
    else [task for task in tasks if task.status == status_map[status_filter]]
)

status_labels = {
    TaskStatus.QUEUED: "排队中",
    TaskStatus.RUNNING: "处理中",
    TaskStatus.SUCCEEDED: "已完成",
    TaskStatus.FAILED: "失败",
}
kind_labels = {
    TaskKind.SEARCH: "候选检索",
    TaskKind.TRANSCRIPTION: "视频音轨转写",
    TaskKind.AVATAR: "数字人生成",
}

with st.container(border=True):
    st.subheader("任务列表")
    task_frame = pd.DataFrame(
        [
            {
                "task_id": task.task_id,
                "任务": task.title,
                "类型": kind_labels[task.kind],
                "状态": status_labels[task.status],
                "处理阶段": getattr(task, "stage", ""),
                "进度": task.progress,
                "创建时间": task.created_at,
                "耗时（秒）": task.elapsed_seconds,
                "重试次数": task.retry_count,
                "错误": task.error_message,
            }
            for task in filtered_tasks
        ]
    )
    event = st.dataframe(
        task_frame,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="task_table",
        column_config={
            "task_id": None,
            "任务": st.column_config.TextColumn(pinned=True),
            "进度": st.column_config.ProgressColumn(
                min_value=0, max_value=100, format="%d%%"
            ),
            "创建时间": st.column_config.DatetimeColumn(format="MM-DD HH:mm:ss"),
            "耗时（秒）": st.column_config.NumberColumn(format="%.1f"),
        },
    )
    if event.selection.rows:
        st.session_state.selected_task_id = str(
            task_frame.iloc[event.selection.rows[0]]["task_id"]
        )

selected_task = transcription_service.repository.get_task(
    st.session_state.get("selected_task_id")
)
if selected_task:
    with st.container(border=True):
        with st.container(
            horizontal=True,
            horizontal_alignment="distribute",
            vertical_alignment="center",
        ):
            st.subheader(selected_task.title)
            render_task_badge(selected_task.status)
        st.caption(
            f"任务编号 {selected_task.task_id} · {kind_labels[selected_task.kind]}"
        )
        st.progress(selected_task.progress, text=f"进度 {selected_task.progress}%")
        if selected_task.error_message:
            st.error(selected_task.error_message, icon=":material/error:")
        if selected_task.outputs:
            st.json(selected_task.outputs, expanded=False)
        if isinstance(selected_task, AvatarTask):
            st.write(
                f"**形象 / 音色：** {selected_task.avatar_name} / "
                f"{selected_task.voice_name}"
            )
            st.write(
                f"**权利主体：** {selected_task.rights_holder} · "
                f"**供应商状态：** {selected_task.provider_status.value}"
            )
            # 检查OUTCOME_UNKNOWN状态
            if selected_task.provider_status == AvatarProviderStatus.OUTCOME_UNKNOWN:
                st.warning(
                    "**任务状态未知**\n\n"
                    "此任务的供应商状态为 `OUTCOME_UNKNOWN`，可能被阻断。\n\n"
                    "您可以强制解锁此任务，将其标记为失败状态。",
                    icon=":material/warning:",
                )
                if st.button(
                    "强制解锁",
                    key=f"force_unlock_{selected_task.task_id}",
                    icon=":material/lock_open:",
                    help="强制解锁此任务，将其标记为失败状态",
                ):
                    try:
                        from datetime import datetime
                        # 更新provider_status为FAILED
                        selected_task.provider_status = AvatarProviderStatus.FAILED
                        selected_task.updated_at = datetime.now().astimezone()
                        selected_task.error_message = "手动强制解锁"
                        transcription_service.repository.save_task(selected_task)
                        st.toast("已强制解锁任务", icon=":material/check_circle:")
                        st.rerun()
                    except Exception as e:
                        st.toast(f"强制解锁失败: {e}", icon=":material/error:")
            if selected_task.result_path and Path(selected_task.result_path).is_file():
                st.video(selected_task.result_path)
        can_retry = (
            selected_task.status == TaskStatus.FAILED
            and selected_task.retry_count < 1
            and getattr(selected_task, "is_mock", False)
        )
        if st.button(
            "重试失败任务",
            icon=":material/refresh:",
            disabled=not can_retry,
            help="每个失败任务最多重试一次。",
        ):
            transcription_service.retry_failed_task(selected_task.task_id)
            st.toast("任务已完成一次重试", icon=":material/check_circle:")
            st.rerun()
        if (
            selected_task.status == TaskStatus.FAILED
            and not can_retry
            and not isinstance(selected_task, AvatarTask)
        ):
            st.info("媒体已在处理结束后安全清理，请返回转文案页重新上传。")
        elif (
            isinstance(selected_task, AvatarTask)
            and selected_task.status == TaskStatus.FAILED
        ):
            st.info("数字人任务不会自动重复提交；请返回数字人页面核对幂等任务状态。")
