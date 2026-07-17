import streamlit as st

from src.app_state import initialize_state
from src.resources import runtime_capabilities

st.set_page_config(
    page_title="短视频热点洞察与智能生产",
    page_icon=":material/monitoring:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

initialize_state()

pages = [
    st.Page(
        "app_pages/candidates.py",
        title="爆火视频检索",
        icon=":material/trending_up:",
        default=True,
    ),
    st.Page(
        "app_pages/transcription.py",
        title="音视频转文案",
        icon=":material/transcribe:",
    ),
    st.Page(
        "app_pages/tasks.py",
        title="任务记录",
        icon=":material/history:",
    ),
]

capabilities = runtime_capabilities()
with st.sidebar:
    st.markdown("**运行环境**")
    st.badge("Mock 模式", color="blue", icon=":material/science:")
    if capabilities["ffmpeg"]:
        st.badge("FFmpeg 已安装", color="green", icon=":material/check_circle:")
    else:
        st.badge("FFmpeg 未安装", color="orange", icon=":material/warning:")
    st.caption("首版不调用真实平台、FFmpeg、ASR 或付费接口。")
    st.caption("框架版本 0.1.0")

navigation = st.navigation(pages, position="top")
navigation.run()
