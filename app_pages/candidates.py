from __future__ import annotations

import pandas as pd
import streamlit as st

from src.app_state import get_services, select_candidate, selected_candidate_id
from src.models import HeatLevel, Platform, VideoCandidate
from src.ui import render_heat_badge, render_page_header

render_page_header(
    "爆火视频检索",
    "在已导入或已授权的数据范围内发现候选；本页数据全部为 Mock 示例。",
    icon="trending_up",
)

candidate_service, _, _ = get_services()
all_candidates = candidate_service.search(published_within_hours=720)
categories = ["全部赛道", *sorted({item.category for item in all_candidates})]

with st.form("candidate_filters"):
    with st.container(horizontal=True, vertical_alignment="bottom"):
        query = st.text_input("关键词", placeholder="例如：防晒、探店、AI 工具")
        platform_labels = st.multiselect(
            "平台",
            options=["抖音", "快手", "小红书"],
            default=["抖音", "快手", "小红书"],
        )
        category = st.selectbox("赛道", categories)
        published_hours = st.selectbox(
            "发布时间",
            options=[24, 72, 168, 720],
            index=3,
            format_func=lambda value: (
                "近 30 天" if value == 720 else f"近 {value} 小时"
            ),
        )
        min_interactions = st.number_input("最低互动量", min_value=0, value=0, step=500)
        submitted = st.form_submit_button(
            "检索 Mock 候选", type="primary", icon=":material/search:"
        )

platform_map = {
    "抖音": Platform.DOUYIN,
    "快手": Platform.KUAISHOU,
    "小红书": Platform.XIAOHONGSHU,
}
platforms = [platform_map[label] for label in platform_labels]
items = candidate_service.search(
    query=query,
    platforms=platforms,
    category=category,
    published_within_hours=published_hours,
    min_interactions=int(min_interactions),
)
if submitted:
    st.toast(
        f"已在 Mock 数据中找到 {len(items)} 条候选", icon=":material/check_circle:"
    )

with st.container(horizontal=True):
    st.metric("候选视频", len(items), border=True)
    st.metric(
        "S / A 级",
        sum(item.heat.level in {HeatLevel.S, HeatLevel.A} for item in items),
        border=True,
    )
    st.metric(
        "平均热度",
        f"{sum(item.heat.score for item in items) / len(items):.1f}" if items else "—",
        border=True,
    )
    st.metric(
        "低置信度", sum(item.metrics.confidence < 0.6 for item in items), border=True
    )


def to_row(candidate: VideoCandidate) -> dict[str, object]:
    metrics = candidate.metrics
    return {
        "video_id": candidate.video_id,
        "标题": candidate.title,
        "平台": {
            Platform.DOUYIN: "抖音",
            Platform.KUAISHOU: "快手",
            Platform.XIAOHONGSHU: "小红书",
        }[candidate.platform],
        "作者": candidate.author_name,
        "赛道": candidate.category,
        "发布时间": candidate.published_at,
        "热度分": candidate.heat.score,
        "等级": candidate.heat.level.value,
        "置信度": candidate.metrics.confidence,
        "点赞": metrics.likes,
        "评论": metrics.comments,
        "收藏": metrics.favorites,
        "分享": metrics.shares,
        "播放": metrics.plays,
        "来源": str(candidate.source_url),
    }


with st.container(border=True):
    st.subheader("热点候选榜")
    st.caption(
        "选择一行查看入榜原因，并可将候选带入转写工作台。缺失指标保持为空，不会显示为 0。"
    )
    dataframe = pd.DataFrame([to_row(item) for item in items])
    if dataframe.empty:
        st.info("当前筛选条件没有匹配候选，请放宽条件后重试。", icon=":material/info:")
        event = None
    else:
        event = st.dataframe(
            dataframe,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="candidate_table",
            column_config={
                "video_id": None,
                "标题": st.column_config.TextColumn(pinned=True),
                "发布时间": st.column_config.DatetimeColumn(format="MM-DD HH:mm"),
                "热度分": st.column_config.ProgressColumn(
                    min_value=0, max_value=100, format="%.1f"
                ),
                "置信度": st.column_config.NumberColumn(format="percent"),
                "点赞": st.column_config.NumberColumn(format="localized"),
                "评论": st.column_config.NumberColumn(format="localized"),
                "收藏": st.column_config.NumberColumn(format="localized"),
                "分享": st.column_config.NumberColumn(format="localized"),
                "播放": st.column_config.NumberColumn(format="localized"),
                "来源": st.column_config.LinkColumn(display_text="查看来源"),
            },
        )
        if event.selection.rows:
            select_candidate(str(dataframe.iloc[event.selection.rows[0]]["video_id"]))

selected = candidate_service.get(selected_candidate_id())
if selected:
    with st.container(border=True):
        with st.container(
            horizontal=True,
            horizontal_alignment="distribute",
            vertical_alignment="center",
        ):
            st.subheader(selected.title)
            render_heat_badge(selected.heat.level)
        st.caption(
            f"{selected.author_name} · {selected.category} · 数据置信度 {selected.metrics.confidence:.0%}"
        )
        for reason in selected.heat.reasons:
            st.markdown(f"- {reason}")
        if selected.heat.provisional:
            st.warning(
                "对比桶少于 100 条，当前等级为 provisional 临时判断。",
                icon=":material/warning:",
            )
        if st.button(
            "进入音视频转文案", type="primary", icon=":material/arrow_forward:"
        ):
            st.switch_page("app_pages/transcription.py")
