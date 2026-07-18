import streamlit as st

from src.app_state import initialize_state

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
        title="音频转文案",
        icon=":material/transcribe:",
    ),
    st.Page(
        "app_pages/tasks.py",
        title="任务记录",
        icon=":material/history:",
    ),
]

navigation = st.navigation(pages, position="top")
navigation.run()
