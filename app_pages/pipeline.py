"""批量生产流水线页面。

提供端到端的短视频批量生产功能：
1. 关键词输入 → 搜索候选
2. 文案改写
3. 视频剪辑
4. 多平台发布
"""

from __future__ import annotations

import threading
import time

import streamlit as st

from src.app_state import get_pipeline_service
from src.models import (
    PipelineRunStatus,
    PipelineStage,
    PublishPlatform,
    TaskStatus,
    VideoEditConfig,
    VideoEditStep,
    VideoEditStepKind,
)


st.header("批量生产流水线", divider="rainbow")
st.caption("关键词驱动 → 文案改写 → 剪辑 → 多平台发布")

# -- 初始化服务 --
pipeline_svc = get_pipeline_service()


def _run_pipeline_background(
    run_id: str,
    keyword: str,
    platforms: list[PublishPlatform],
    edit_config: VideoEditConfig,
    style_prompt: str,
    target_length: int,
    tone: str,
) -> None:
    """后台线程中执行流水线。"""
    try:
        pipeline_svc.execute_run(
            run_id=run_id,
            keyword=keyword,
            platforms=platforms,
            edit_config=edit_config,
            style_prompt=style_prompt,
            target_length=target_length,
            tone=tone,
        )
    except Exception:
        # execute_run 内部已处理异常标记，此处兜底
        pass


# -- Tab 布局 --
tab_new, tab_history = st.tabs(["新建流水线", "流水线记录"])

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
            format_func=lambda x: {"default": "标准白字", "highlight": "高亮黄字"}.get(
                x, x
            ),
            key="sub_style",
        )
        trim_start = st.number_input("裁剪起始(秒)", min_value=0.0, value=0.0, step=0.5)
        trim_duration = st.number_input(
            "裁剪时长(秒, 0=不裁剪)", min_value=0.0, value=0.0, step=1.0
        )
        speed = st.slider("播放速度", 0.5, 2.0, 1.0, 0.1, key="speed")

    if st.button(
        "启动流水线",
        type="primary",
        use_container_width=True,
        disabled=not keyword.strip(),
    ):
        # 创建流水线
        platforms_list: list[PublishPlatform] = []
        if pub_douyin:
            platforms_list.append(PublishPlatform.DOUYIN)
        if pub_kuaishou:
            platforms_list.append(PublishPlatform.KUAISHOU)
        if pub_wechat:
            platforms_list.append(PublishPlatform.WECHAT_CHANNELS)
        if not platforms_list:
            st.error("请至少选择一个发布平台。")
            st.stop()

        # 构建剪辑配置
        steps: list[VideoEditStep] = []
        order = 0
        if trim_start > 0 or trim_duration > 0:
            params: dict = {"start": trim_start}
            if trim_duration > 0:
                params["duration"] = trim_duration
            steps.append(
                VideoEditStep(kind=VideoEditStepKind.TRIM, params=params, order=order)
            )
            order += 1
        if speed != 1.0:
            steps.append(
                VideoEditStep(
                    kind=VideoEditStepKind.SPEED, params={"speed": speed}, order=order
                )
            )
            order += 1
        edit_config = VideoEditConfig(steps=steps)

        # 创建流水线记录
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
        st.session_state.pipeline_running = True

        # 启动后台线程执行流水线
        thread = threading.Thread(
            target=_run_pipeline_background,
            kwargs=dict(
                run_id=run.run_id,
                keyword=keyword,
                platforms=platforms_list,
                edit_config=edit_config,
                style_prompt=style_prompt,
                target_length=target_length,
                tone=tone,
            ),
            daemon=True,
        )
        thread.start()
        st.success(f"流水线已启动: {run.run_id}")
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
            status_label = {
                PipelineRunStatus.PENDING: "等待中",
                PipelineRunStatus.RUNNING: "执行中",
                PipelineRunStatus.SUCCEEDED: "已完成",
                PipelineRunStatus.FAILED: "失败",
                PipelineRunStatus.PARTIAL: "部分完成",
                PipelineRunStatus.PAUSED: "已暂停",
            }.get(run.status, run.status.value)
            st.markdown(f"**状态**: {status_label}")

            if run.error_message:
                st.error(run.error_message)

            # 各阶段状态
            stages_display = [
                ("关键词搜索", PipelineStage.KEYWORD_SEARCH),
                ("文案改写", PipelineStage.COPYWRITING),
                ("视频剪辑", PipelineStage.VIDEO_EDITING),
                ("多平台发布", PipelineStage.PUBLISHING),
            ]
            for label, stage in stages_display:
                step = next((s for s in run.stages if s.stage == stage), None)
                if step:
                    icon = (
                        "✅"
                        if step.status == TaskStatus.SUCCEEDED
                        else "🔄"
                        if step.status == TaskStatus.RUNNING
                        else "❌"
                    )
                    st.markdown(f"{icon} **{label}**: {step.status.value}")
                    if step.error_message:
                        st.caption(f"  错误: {step.error_message}")
                else:
                    st.markdown(f"⬜ **{label}**: 等待中")

            # 自动轮询：流水线仍在运行时自动刷新
            if run.status in {PipelineRunStatus.PENDING, PipelineRunStatus.RUNNING}:
                time.sleep(1)
                st.rerun()

            # 操作按钮
            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("清除当前流水线", key="clear_pipeline"):
                    del st.session_state.current_pipeline
                    if "pipeline_running" in st.session_state:
                        del st.session_state.pipeline_running
                    st.rerun()
            with col_b:
                if run.status in {PipelineRunStatus.FAILED, PipelineRunStatus.PARTIAL}:
                    if st.button("重试失败步骤", key="retry_pipeline"):
                        st.info("重试功能需要手动重新启动流水线。")

# ============================================================
# Tab 2: 流水线记录
# ============================================================
with tab_history:
    st.subheader("历史流水线记录")
    runs = pipeline_svc.list_runs(limit=50)
    if not runs:
        st.info("暂无流水线记录。")
    else:
        for run_item in runs:
            status_icon = {
                PipelineRunStatus.SUCCEEDED: "✅",
                PipelineRunStatus.RUNNING: "🔄",
                PipelineRunStatus.FAILED: "❌",
                PipelineRunStatus.PENDING: "⏳",
                PipelineRunStatus.PAUSED: "⏸️",
                PipelineRunStatus.PARTIAL: "⚠️",
            }.get(run_item.status, "❓")
            with st.expander(
                f"{status_icon} {run_item.keyword} - {run_item.status.value}"
                f" ({run_item.created_at.strftime('%m-%d %H:%M')})"
            ):
                st.json(run_item.model_dump(mode="json"), expanded=False)
