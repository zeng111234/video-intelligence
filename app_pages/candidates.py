from __future__ import annotations

import pandas as pd
import streamlit as st

from src.app_state import (
    get_keyword_discovery_service,
    get_keyword_trend_service,
    get_repository,
    get_services,
    get_source_service,
    select_candidate,
    selected_candidate_id,
)
from src.config import build_douyin_keyword_adapter
from src.backup_import_ui import render_backup_imports
from src.models import (
    DiscoveryResult,
    HeatLevel,
    Platform,
    VideoCandidate,
)
from src.ui import render_heat_badge, render_page_header

render_page_header(
    "爆火视频检索",
    "输入行业或内容关键词，从已获授权的平台能力获取候选并执行热点规则。",
    icon="trending_up",
)

candidate_service, _, _ = get_services()
repository = get_repository()
source_service = get_source_service()
discovery_service = get_keyword_discovery_service()
trend_service = get_keyword_trend_service()
douyin_adapter = build_douyin_keyword_adapter(st.secrets)
douyin_capability = douyin_adapter.capabilities()

with st.container(border=True):
    st.subheader("低调用量关键词热门榜")
    st.caption(
        "平台综合排序只负责召回 10 条候选，系统再用近 7 天点赞增长、视频年龄、"
        "综合名次和持续入榜次数计算自己的 Top 10。每次获取固定只调用 1 次搜索接口。"
    )
    with st.form("platform_keyword_discovery"):
        with st.container(horizontal=True, vertical_alignment="bottom"):
            platform_keyword = st.text_input(
                "平台关键词",
                placeholder="例如：二手车",
                key="platform_keyword_input",
            )
            publish_window_label = st.selectbox(
                "召回时间范围",
                ["近 24 小时", "近 7 天"],
                key="platform_publish_window",
            )
            discover_submitted = st.form_submit_button(
                "获取综合候选10条（1次调用）",
                type="primary",
                icon=":material/travel_explore:",
                disabled=not douyin_capability.enabled,
            )
            recompute_submitted = st.form_submit_button(
                "仅重新计算本地7日榜（0次调用）",
                icon=":material/calculate:",
            )
    if douyin_capability.enabled:
        st.success(
            "ClientKey/ClientSecret 已配置；首次调用后由抖音返回实际权限状态。",
            icon=":material/key:",
        )
    else:
        st.warning(
            "功能已就绪，等待配置 DOUYIN_CLIENT_KEY 和 DOUYIN_CLIENT_SECRET。"
            "当前不会发起任何平台请求。",
            icon=":material/key_off:",
        )
        st.code(
            'DOUYIN_CLIENT_KEY = ""\nDOUYIN_CLIENT_SECRET = ""',
            language="toml",
        )

    if discover_submitted:
        try:
            publish_time = 1 if publish_window_label == "近 24 小时" else 7
            with st.spinner("正在获取 10 条综合候选并计算近 7 天自有榜单……"):
                discovery_result = discovery_service.discover(
                    keyword=platform_keyword,
                    adapter=douyin_adapter,
                    publish_time=publish_time,
                )
                trend_service.recompute(platform_keyword)
            st.session_state["last_discovery_result"] = discovery_result.model_dump(
                mode="json"
            )
            st.session_state["candidate_local_query"] = platform_keyword.strip()
            st.session_state["active_trend_keyword"] = platform_keyword.strip()
            st.rerun()
        except Exception as exc:
            st.error(str(exc), icon=":material/error:")

    if recompute_submitted:
        try:
            trend_service.recompute(platform_keyword)
            st.session_state["active_trend_keyword"] = platform_keyword.strip()
            st.toast("已使用本地数据重算，未调用平台接口。", icon=":material/check:")
            st.rerun()
        except Exception as exc:
            st.error(str(exc), icon=":material/error:")

    raw_discovery = st.session_state.get("last_discovery_result")
    if raw_discovery:
        last_discovery = DiscoveryResult.model_validate(raw_discovery)
        with st.container(horizontal=True):
            st.metric(
                "最近关键词",
                last_discovery.keyword,
                border=True,
            )
            st.metric(
                "唯一候选",
                f"{last_discovery.unique_count}/{last_discovery.requested_count}",
                border=True,
            )
            st.metric(
                "搜索调用",
                f"{last_discovery.api_call_count} 次",
                border=True,
            )
            st.metric(
                "执行状态",
                "部分完成" if last_discovery.partial else "完成",
                border=True,
            )
            st.metric(
                "最近调用",
                last_discovery.finished_at.astimezone().strftime("%m-%d %H:%M"),
                border=True,
            )
        if last_discovery.errors:
            for error in last_discovery.errors:
                st.warning(error.message)

    trend_keyword = st.session_state.get("active_trend_keyword", "").strip()
    trend_results = (
        repository.list_keyword_trend_results(trend_keyword, limit=10)
        if trend_keyword
        else []
    )
    if trend_results:
        candidate_lookup = {
            candidate.video_id: candidate for candidate in repository.list_candidates()
        }
        trend_rows = []
        for own_rank, trend in enumerate(trend_results, start=1):
            candidate = candidate_lookup.get(trend.candidate_id)
            if candidate is None:
                continue
            trend_rows.append(
                {
                    "自有排名": own_rank,
                    "标题": candidate.title,
                    "趋势分": trend.score,
                    "状态": trend.level.value,
                    "平台综合召回位置": trend.platform_rank,
                    "点赞增长/小时": trend.like_growth_per_hour,
                    "年龄归一化点赞/小时": trend.likes_per_hour,
                    "近7天入榜次数": trend.appearance_count,
                    "历史池": trend.pool_size,
                    "置信度": trend.confidence,
                    "异常": (
                        "疑似异常待核验"
                        if trend.anomaly_status.value == "suspected"
                        else "正常"
                    ),
                    "为何入榜/异常说明": "；".join(trend.reasons),
                }
            )
        st.markdown(f"**系统自有 Top 10 · {trend_keyword}**")
        st.dataframe(
            pd.DataFrame(trend_rows),
            hide_index=True,
            column_config={
                "趋势分": st.column_config.ProgressColumn(
                    "趋势分", min_value=0, max_value=100
                ),
                "置信度": st.column_config.NumberColumn("置信度", format="percent"),
            },
        )
        st.caption(
            "该排名由系统公式计算；平台综合位置仅是一个分量。样本池少于 30 条时只显示“观察中”。"
        )

st.subheader("搜索本地候选")
all_candidates = candidate_service.search(
    platforms=[Platform.DOUYIN], published_within_hours=720
)
categories = ["全部赛道", *sorted({item.category for item in all_candidates})]

with st.form("candidate_filters"):
    with st.container(horizontal=True, vertical_alignment="bottom"):
        query = st.text_input(
            "关键词",
            placeholder="例如：二手车",
            key="candidate_local_query",
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
            "检索候选", type="primary", icon=":material/search:"
        )

items = candidate_service.search(
    query=query,
    platforms=[Platform.DOUYIN],
    category=category,
    published_within_hours=published_hours,
    min_interactions=int(min_interactions),
)
if submitted:
    st.toast(f"已找到 {len(items)} 条候选", icon=":material/check_circle:")

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
    hotspot_status = {
        HeatLevel.S: "是（S）",
        HeatLevel.A: "是（A）",
        HeatLevel.B: "是（B）",
        HeatLevel.STATIC_HIGH: "静态高热",
        HeatLevel.ANOMALOUS: "异常待核验",
        HeatLevel.NORMAL: "否",
        HeatLevel.INSUFFICIENT: "数据不足",
    }[candidate.heat.level]
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
        "命中关键词": "、".join(candidate.matched_by),
        "匹配状态": candidate.eligibility_status.value,
        "发布时间": candidate.published_at,
        "热度分": candidate.heat.score,
        "等级": candidate.heat.level.value,
        "是否热点": hotspot_status,
        "置信度": candidate.metrics.confidence,
        "快照": len(repository.list_snapshots(candidate.video_id)),
        "桶样本": candidate.heat.bucket_sample_size,
        "官方榜": "是" if candidate.official_hot else "否",
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
        if selected.heat.bucket_definition:
            st.caption(
                f"对比桶：{selected.heat.bucket_definition} · 样本 {selected.heat.bucket_sample_size} · "
                f"快照 {selected.heat.snapshot_count}"
            )
        for reason in selected.heat.reasons:
            st.markdown(f"- {reason}")
        if selected.heat.provisional:
            st.warning(
                "当前等级为 provisional 试运行判断；达到稳定样本与置信度门槛后才转为正式结论。",
                icon=":material/warning:",
            )
        if st.button("将此视频转成文案", type="primary", icon=":material/transcribe:"):
            st.switch_page("app_pages/transcription.py")

st.divider()
with st.expander("备用导入与数据同步", icon=":material/database:"):
    render_backup_imports(source_service, repository)
