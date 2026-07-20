import streamlit as st

from collections.abc import MutableMapping
from typing import Any

from src.app_state import initialize_state, get_context_budget

st.set_page_config(
    page_title="短视频热点洞察与智能生产",
    page_icon=":material/monitoring:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

initialize_state()

# 预算检查：在页面导航执行前检查内存预算
context_budget = get_context_budget()
# 将SessionStateProxy转换为类型安全的字典
session_state_dict: MutableMapping[str, Any] = {str(k): v for k, v in st.session_state.items()}
is_within_budget, current_mb, budget_mb = context_budget.check_session_budget(
    session_state_dict
)
if not is_within_budget:
    st.error(
        f"**内存预算超限警告**\n\n"
        f"当前会话状态内存占用 {current_mb:.1f}MB 已超过预算 {budget_mb:.1f}MB。\n\n"
        f"为防止应用崩溃，已停止页面渲染。请刷新页面或联系管理员。",
        icon=":material/warning:",
    )
    st.stop()

pages = [
    st.Page(
        "app_pages/candidates.py",
        title="爆火视频检索",
        icon=":material/trending_up:",
        default=True,
    ),
    st.Page(
        "app_pages/transcription.py",
        title="视频音轨转文案",
        icon=":material/transcribe:",
    ),
    st.Page(
        "app_pages/avatar_generation.py",
        title="数字人生成",
        icon=":material/smart_toy:",
    ),
    st.Page(
        "app_pages/pipeline.py",
        title="批量生产流水线",
        icon=":material/auto_awesome:",
    ),
    st.Page(
        "app_pages/tasks.py",
        title="任务记录",
        icon=":material/history:",
    ),
    st.Page(
        "app_pages/admin.py",
        title="系统管理",
        icon=":material/admin_panel_settings:",
    ),
]

navigation = st.navigation(pages, position="top")
navigation.run()
