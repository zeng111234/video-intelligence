from __future__ import annotations

import streamlit as st
from datetime import datetime

from src.app_state import get_repository, get_context_budget
from src.models import (
    AvatarTask,
    PlatformRunStatus,
    AvatarProviderStatus,
)

# 页面标题
st.title("系统管理")
st.caption("监控系统状态、管理任务阻断、查看预算使用情况")

# 获取仓库和预算管理器
repository = get_repository()
context_budget = get_context_budget()

# 显示预算状态
st.subheader("预算监控")
budget_status = context_budget.get_status()
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric(
        "最大并发任务",
        budget_status["max_concurrent_tasks"],
        border=True,
    )
with col2:
    st.metric(
        "活跃任务数",
        budget_status["active_tasks"],
        border=True,
    )
with col3:
    st.metric(
        "可用槽位",
        budget_status["available_slots"],
        border=True,
    )
with col4:
    st.metric(
        "内存预算",
        f"{budget_status['session_state_budget_mb']:.0f} MB",
        border=True,
    )

# 检查当前内存使用
is_within_budget, current_mb, budget_mb = context_budget.check_session_budget(
    dict(st.session_state)
)
if not is_within_budget:
    st.error(
        f"当前内存使用 {current_mb:.1f}MB 已超过预算 {budget_mb:.1f}MB",
        icon=":material/warning:",
    )
else:
    st.success(
        f"内存使用正常：{current_mb:.1f}MB / {budget_mb:.1f}MB",
        icon=":material/check_circle:",
    )

st.divider()

# 平台搜索任务管理
st.subheader("平台搜索任务管理")
st.write("筛选并解除阻断的平台搜索任务")

# 获取所有搜索批次
try:
    search_batches = repository.list_search_batches(limit=100)
    all_platform_runs = []
    for batch in search_batches:
        runs = repository.list_platform_search_runs(batch.batch_id)
        all_platform_runs.extend(runs)
except Exception as e:
    st.error(f"获取平台搜索任务失败: {e}")
    all_platform_runs = []

# 筛选OUTCOME_UNKNOWN状态
unknown_runs = [
    run for run in all_platform_runs if run.status == PlatformRunStatus.OUTCOME_UNKNOWN
]

st.metric("OUTCOME_UNKNOWN 任务", len(unknown_runs), border=True)

if unknown_runs:
    for run in unknown_runs:
        with st.container(border=True):
            col1, col2, col3 = st.columns([2, 2, 1])
            with col1:
                st.write(f"**平台:** {run.platform.value}")
                st.write(f"**供应商:** {run.provider}")
                st.write(f"**批次ID:** {run.batch_id}")
            with col2:
                st.write(f"**运行ID:** {run.run_id}")
                st.write(f"**请求计数:** {run.requested_count}")
                st.write(f"**返回计数:** {run.returned_count}")
                st.write(
                    f"**开始时间:** {run.started_at.strftime('%Y-%m-%d %H:%M:%S')}"
                )
            with col3:
                st.write(f"**状态:** {run.status.value}")
                st.write(f"**指纹:** {run.request_fingerprint[:16]}...")

                # 解除阻断按钮
                if st.button(
                    "解除阻断",
                    key=f"resolve_{run.run_id}",
                    icon=":material/lock_open:",
                    help="解除此任务的阻断状态",
                ):
                    try:
                        repository.resolve_platform_search_request(
                            run.request_fingerprint
                        )
                        st.toast("已成功解除阻断状态", icon=":material/check_circle:")
                        st.rerun()
                    except Exception as e:
                        st.toast(f"解除阻断失败: {e}", icon=":material/error:")
else:
    st.info("没有找到状态为 OUTCOME_UNKNOWN 的平台搜索任务")

st.divider()

# 数字人任务管理
st.subheader("数字人任务管理")
st.write("筛选并处理阻断的数字人任务")

# 获取所有任务
try:
    all_tasks = repository.list_tasks()
    avatar_tasks = [task for task in all_tasks if isinstance(task, AvatarTask)]
except Exception as e:
    st.error(f"获取数字人任务失败: {e}")
    avatar_tasks = []

# 筛选OUTCOME_UNKNOWN状态
unknown_avatar_tasks = [
    task
    for task in avatar_tasks
    if task.provider_status == AvatarProviderStatus.OUTCOME_UNKNOWN
]

st.metric("OUTCOME_UNKNOWN 数字人任务", len(unknown_avatar_tasks), border=True)

if unknown_avatar_tasks:
    for task in unknown_avatar_tasks:
        with st.container(border=True):
            col1, col2, col3 = st.columns([2, 2, 1])
            with col1:
                st.write(f"**任务ID:** {task.task_id}")
                st.write(f"**标题:** {task.title}")
                st.write(f"**形象:** {task.avatar_name}")
                st.write(f"**音色:** {task.voice_name}")
            with col2:
                st.write(f"**供应商状态:** {task.provider_status.value}")
                st.write(
                    f"**创建时间:** {task.created_at.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                st.write(f"**重试次数:** {task.retry_count}")
                if task.error_message:
                    st.write(f"**错误:** {task.error_message}")
            with col3:
                st.write(f"**任务状态:** {task.status.value}")

                # 标记为失败并解锁按钮
                if st.button(
                    "标记为失败并解锁",
                    key=f"unlock_{task.task_id}",
                    icon=":material/lock_open:",
                    help="将任务状态标记为失败并解锁",
                ):
                    try:
                        # 更新provider_status为FAILED
                        task.provider_status = AvatarProviderStatus.FAILED
                        task.updated_at = datetime.now().astimezone()
                        task.error_message = "手动标记为失败并解锁"
                        repository.save_task(task)
                        st.toast("已标记为失败并解锁", icon=":material/check_circle:")
                        st.rerun()
                    except Exception as e:
                        st.toast(f"操作失败: {e}", icon=":material/error:")
else:
    st.info("没有找到状态为 OUTCOME_UNKNOWN 的数字人任务")

st.divider()

# 系统信息
st.subheader("系统信息")
st.write("当前系统运行状态和配置信息")

col1, col2 = st.columns(2)
with col1:
    st.metric(
        "总搜索批次",
        len(search_batches) if "search_batches" in locals() else 0,
        border=True,
    )
with col2:
    st.metric(
        "总数字人任务",
        len(avatar_tasks) if "avatar_tasks" in locals() else 0,
        border=True,
    )
