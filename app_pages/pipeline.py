"""批量生产流水线页面。

提供端到端的短视频批量生产功能：
1. 关键词输入 → 搜索候选
2. 文案改写
3. 数字人生成
4. 视频剪辑
5. 多平台发布
"""

from __future__ import annotations

import streamlit as st

from src.adapters.llm import build_copywriting_engine
from src.adapters.publishers.sandbox import SandboxPublisher
from src.adapters.video_editor import FFmpegVideoEditor
from src.models import (
    PipelineRunStatus,
    PipelineStage,
    PublishPlatform,
    TaskStatus,
    VideoEditConfig,
    VideoEditStep,
    VideoEditStepKind,
)
from src.repositories.sqlite import SQLiteRepository
from src.services.copywriting import CopywritingService
from src.services.pipeline import PipelineService
from src.services.publisher import PublishService
from src.services.video_editor import VideoEditingService


def _get_repository() -> SQLiteRepository:
    if "repository" not in st.session_state:
        st.session_state.repository = SQLiteRepository("data/video_intelligence.db")
    return st.session_state.repository


def _get_pipeline_service() -> PipelineService:
    return PipelineService(_get_repository())


def _get_copywriting_service() -> CopywritingService:
    if "copywriting_service" not in st.session_state:
        repo = _get_repository()
        engine = build_copywriting_engine()
        st.session_state.copywriting_service = CopywritingService(repo, engine)
    return st.session_state.copywriting_service


def _get_video_editing_service() -> VideoEditingService:
    if "video_editing_service" not in st.session_state:
        repo = _get_repository()
        editor = FFmpegVideoEditor()
        st.session_state.video_editing_service = VideoEditingService(repo, editor)
    return st.session_state.video_editing_service


def _get_publish_service() -> PublishService:
    if "publish_service" not in st.session_state:
        repo = _get_repository()
        publishers = {
            "douyin": SandboxPublisher(PublishPlatform.DOUYIN),
            "kuaishou": SandboxPublisher(PublishPlatform.KUAISHOU),
            "wechat_channels": SandboxPublisher(PublishPlatform.WECHAT_CHANNELS),
        }
        st.session_state.publish_service = PublishService(repo, publishers)
    return st.session_state.publish_service


st.header("批量生产流水线", divider="rainbow")
st.caption("关键词驱动 → 文案改写 → 数字人 → 剪辑 → 发布")

# -- 初始化服务 --
pipeline_svc = _get_pipeline_service()
copy_svc = _get_copywriting_service()
edit_svc = _get_video_editing_service()
pub_svc = _get_publish_service()

# -- 侧边栏：能力检测 --
with st.sidebar:
    st.subheader("系统能力")
    copy_cap = copy_svc.capabilities()
    edit_cap = edit_svc.capabilities()
    st.markdown(f"**文案引擎**: {copy_cap.get('display_name', '-')}")
    st.markdown(f"- 状态: {'可用' if copy_cap.get('enabled') else '不可用'}")
    st.markdown(f"- 模式: {copy_cap.get('mode', '-')}")
    st.markdown(f"**视频剪辑**: {edit_cap.get('display_name', '-')}")
    st.markdown(f"- 状态: {'可用' if edit_cap.get('enabled') else '不可用'}")
    platforms = pub_svc.available_platforms()
    st.markdown("**发布平台**:")
    for p in platforms:
        icon = "✅" if p["enabled"] else "⬜"
        st.markdown(f"- {icon} {p['display_name']}")

# -- Tab 布局 --
tab_new, tab_history, tab_copy, tab_edit = st.tabs([
    "新建流水线", "流水线记录", "文案改写", "视频剪辑"
])

# ============================================================
# Tab 1: 新建流水线
# ============================================================
with tab_new:
    st.subheader("创建批量生产流水线")

    col1, col2 = st.columns(2)
    with col1:
        keyword = st.text_input(
            "关键词",
            placeholder="输入视频主题关键词",
            key="pipeline_keyword",
        )
        style_prompt = st.text_area(
            "文案风格要求",
            placeholder="如：专业口播、轻松种草、故事型...",
            height=80,
            key="pipeline_style",
        )
    with col2:
        target_length = st.slider(
            "目标文案字数",
            min_value=50,
            max_value=1000,
            value=300,
            step=50,
            key="pipeline_length",
        )
        tone = st.selectbox(
            "语气",
            ["professional", "casual", "humorous", "serious", "emotional"],
            format_func=lambda x: {
                "professional": "专业",
                "casual": "轻松",
                "humorous": "幽默",
                "serious": "严肃",
                "emotional": "情感",
            }.get(x, x),
            key="pipeline_tone",
        )

    # 平台选择
    st.markdown("**发布平台**")
    col_p1, col_p2, col_p3 = st.columns(3)
    with col_p1:
        pub_douyin = st.checkbox("抖音", value=True, key="pub_douyin")
    with col_p2:
        pub_kuaishou = st.checkbox("快手", value=False, key="pub_kuaishou")
    with col_p3:
        pub_wechat = st.checkbox("视频号", value=False, key="pub_wechat")

    # 剪辑选项
    with st.expander("剪辑选项", expanded=False):
        add_subtitles = st.checkbox("自动添加字幕", value=True, key="add_subs")
        subtitle_style = st.selectbox(
            "字幕样式",
            ["default", "highlight"],
            format_func=lambda x: {"default": "标准白字", "highlight": "高亮黄字"}.get(x, x),
            key="sub_style",
        )
        trim_start = st.number_input("裁剪起始(秒)", min_value=0.0, value=0.0, step=0.5)
        trim_duration = st.number_input(
            "裁剪时长(秒, 0=不裁剪)", min_value=0.0, value=0.0, step=1.0
        )
        speed = st.slider("播放速度", 0.5, 2.0, 1.0, 0.1, key="speed")

    if st.button("启动流水线", type="primary", use_container_width=True, disabled=not keyword.strip()):
        # 创建流水线
        platforms_list = []
        if pub_douyin:
            platforms_list.append(PublishPlatform.DOUYIN)
        if pub_kuaishou:
            platforms_list.append(PublishPlatform.KUAISHOU)
        if pub_wechat:
            platforms_list.append(PublishPlatform.WECHAT_CHANNELS)
        if not platforms_list:
            st.error("请至少选择一个发布平台。")
            st.stop()

        run = pipeline_svc.create_run(
            keyword=keyword,
            config={
                "style_prompt": style_prompt,
                "target_length": target_length,
                "tone": tone,
                "platforms": [p.value for p in platforms_list],
            },
        )
        st.session_state.current_pipeline = run.run_id
        st.success(f"流水线已创建: {run.run_id}")
        st.rerun()

    # 显示当前流水线进度
    if "current_pipeline" in st.session_state:
        run_id = st.session_state.current_pipeline
        run = pipeline_svc.get_run(run_id)
        if run:
            st.markdown("---")
            st.subheader(f"流水线进度: {run.keyword}")

            # 进度条
            stage_progress = {
                PipelineStage.KEYWORD_SEARCH: 20,
                PipelineStage.COPYWRITING: 40,
                PipelineStage.AVATAR_GENERATION: 60,
                PipelineStage.VIDEO_EDITING: 80,
                PipelineStage.PUBLISHING: 100,
            }
            current_pct = 0
            if run.current_stage:
                current_pct = stage_progress.get(run.current_stage, 0)
                if run.status == PipelineRunStatus.SUCCEEDED:
                    current_pct = 100

            st.progress(current_pct / 100)
            st.markdown(f"**状态**: {run.status.value}")

            # 各阶段状态
            stages_display = [
                ("关键词搜索", PipelineStage.KEYWORD_SEARCH),
                ("文案改写", PipelineStage.COPYWRITING),
                ("数字人生成", PipelineStage.AVATAR_GENERATION),
                ("视频剪辑", PipelineStage.VIDEO_EDITING),
                ("多平台发布", PipelineStage.PUBLISHING),
            ]
            for label, stage in stages_display:
                step = next((s for s in run.stages if s.stage == stage), None)
                if step:
                    icon = "✅" if step.status == TaskStatus.SUCCEEDED else "🔄" if step.status == TaskStatus.RUNNING else "❌"
                    st.markdown(f"{icon} **{label}**: {step.status.value}")
                    if step.error_message:
                        st.caption(f"  错误: {step.error_message}")
                else:
                    st.markdown(f"⬜ **{label}**: 等待中")

            # 操作按钮
            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("清除当前流水线", key="clear_pipeline"):
                    del st.session_state.current_pipeline
                    st.rerun()
            with col_b:
                if run.status in {PipelineRunStatus.FAILED, PipelineRunStatus.PARTIAL}:
                    if st.button("重试失败步骤", key="retry_pipeline"):
                        st.info("重试功能需要手动执行各步骤。")

# ============================================================
# Tab 2: 流水线记录
# ============================================================
with tab_history:
    st.subheader("历史流水线记录")
    runs = pipeline_svc.list_runs(limit=50)
    if not runs:
        st.info("暂无流水线记录。")
    else:
        for run in runs:
            status_icon = {
                PipelineRunStatus.SUCCEEDED: "✅",
                PipelineRunStatus.RUNNING: "🔄",
                PipelineRunStatus.FAILED: "❌",
                PipelineRunStatus.PENDING: "⏳",
                PipelineRunStatus.PAUSED: "⏸️",
                PipelineRunStatus.PARTIAL: "⚠️",
            }.get(run.status, "❓")
            with st.expander(
                f"{status_icon} {run.keyword} - {run.status.value} ({run.created_at.strftime('%m-%d %H:%M')})"
            ):
                st.json(run.model_dump(mode="json"), expanded=False)

# ============================================================
# Tab 3: 文案改写
# ============================================================
with tab_copy:
    st.subheader("文案改写引擎")
    st.caption("基于 LLM 的文案复刻 / 改写，支持多种风格和变体。")

    cap = copy_svc.capabilities()
    st.markdown(f"**引擎**: {cap.get('display_name', '-')} | **模式**: {cap.get('mode', '-')}")

    source_text = st.text_area(
        "输入原文",
        height=150,
        placeholder="粘贴需要改写的原始文案...",
        key="copy_source",
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        copy_style = st.text_input("风格", placeholder="如：口播、种草", key="copy_style")
    with col2:
        copy_tone = st.selectbox(
            "语气",
            ["professional", "casual", "humorous"],
            format_func=lambda x: {"professional": "专业", "casual": "轻松", "humorous": "幽默"}.get(x, x),
            key="copy_tone",
        )
    with col3:
        variant_count = st.number_input("变体数量", min_value=1, max_value=5, value=1, key="copy_variants")

    if st.button("开始改写", type="primary", disabled=not source_text.strip(), use_container_width=True):
        with st.spinner("正在改写..."):
            task = copy_svc.rewrite(
                source_text=source_text,
                style_prompt=copy_style,
                tone=copy_tone,
                variant_count=variant_count,
            )
        if task.status == TaskStatus.SUCCEEDED:
            st.success("改写完成!")
            for i, variant in enumerate(task.result_variants, 1):
                st.markdown(f"**变体 {i}**:")
                st.text_area(
                    f"result_{i}",
                    value=variant,
                    height=100,
                    key=f"copy_result_{i}",
                    label_visibility="collapsed",
                )
        else:
            st.error(f"改写失败: {task.error_message}")

    # 历史记录
    st.markdown("---")
    st.subheader("改写历史")
    copy_tasks = copy_svc.list_tasks()
    if copy_tasks:
        for t in copy_tasks[:10]:
            with st.expander(f"{t.title} - {t.status.value}"):
                st.markdown(f"**原文**: {t.source_text[:100]}...")
                if t.result_text:
                    st.markdown(f"**结果**: {t.result_text}")

# ============================================================
# Tab 4: 视频剪辑
# ============================================================
with tab_edit:
    st.subheader("视频剪辑工作台")
    st.caption("基于 FFmpeg 的视频裁剪、字幕、水印、速度调整。")

    cap = edit_svc.capabilities()
    st.markdown(f"**引擎**: {cap.get('display_name', '-')} | **状态**: {'可用' if cap.get('enabled') else '不可用'}")

    uploaded_video = st.file_uploader(
        "上传视频",
        type=["mp4", "mov"],
        key="edit_upload",
    )

    if uploaded_video:
        st.video(uploaded_video)

        # 剪辑参数
        col1, col2 = st.columns(2)
        with col1:
            edit_trim_start = st.number_input("裁剪起始(秒)", 0.0, step=0.5, key="edit_trim_start")
            edit_trim_duration = st.number_input("裁剪时长(秒, 0=不裁)", 0.0, step=1.0, key="edit_trim_dur")
        with col2:
            edit_speed = st.slider("播放速度", 0.25, 4.0, 1.0, 0.25, key="edit_speed")
            edit_resolution = st.selectbox(
                "输出分辨率",
                ["1080x1920", "720x1280", "1280x720", "1920x1080"],
                key="edit_res",
            )

        subtitle_text = st.text_area("字幕文本(留空=无字幕)", height=80, key="edit_subs")

        if st.button("开始剪辑", type="primary", use_container_width=True):
            import tempfile
            from pathlib import Path

            # 保存上传文件到临时目录
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp.write(uploaded_video.read())
                tmp_path = tmp.name

            # 构建剪辑步骤
            steps: list[VideoEditStep] = []
            order = 0
            if edit_trim_start > 0 or edit_trim_duration > 0:
                params: dict = {"start": edit_trim_start}
                if edit_trim_duration > 0:
                    params["duration"] = edit_trim_duration
                steps.append(VideoEditStep(kind=VideoEditStepKind.TRIM, params=params, order=order))
                order += 1
            if edit_speed != 1.0:
                steps.append(VideoEditStep(kind=VideoEditStepKind.SPEED, params={"speed": edit_speed}, order=order))
                order += 1

            config = VideoEditConfig(
                steps=steps,
                output_resolution=edit_resolution,
            )

            with st.spinner("正在剪辑..."):
                task = edit_svc.edit_video(
                    source_video_path=tmp_path,
                    edit_config=config,
                    subtitle_text=subtitle_text if subtitle_text.strip() else None,
                )

            # 清理临时文件
            Path(tmp_path).unlink(missing_ok=True)

            if task.status == TaskStatus.SUCCEEDED:
                st.success("剪辑完成!")
                if task.result_path:
                    st.video(task.result_path)
            else:
                st.error(f"剪辑失败: {task.error_message}")
