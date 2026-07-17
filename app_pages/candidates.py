from __future__ import annotations

from datetime import datetime

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


def duplicate_request_cooldown(keyword: str, publish_time: int, count: int) -> int:
    for last in repository.list_discovery_results(limit=50):
        same_request = (
            last.keyword.casefold() == keyword.strip().casefold()
            and last.publish_time == publish_time
            and last.requested_count == count
            and last.api_call_count == 1
        )
        if same_request:
            elapsed = (datetime.now().astimezone() - last.finished_at).total_seconds()
            return max(0, 60 - int(elapsed))
    return 0


@st.dialog("确认获取热门视频")
def confirm_keyword_discovery(
    keyword: str, publish_window_label: str, count: int
) -> None:
    publish_time = 1 if publish_window_label == "近 24 小时" else 7
    st.write(f"关键词：**{keyword.strip() or '未填写'}**")
    st.write(f"时间范围：**{publish_window_label}**")
    st.write(f"获取数量：**{count} 条**")
    st.warning(
        "确认后将调用 1 次抖音搜索接口，系统不会自动翻页。",
        icon=":material/paid:",
    )
    cooldown = duplicate_request_cooldown(keyword, publish_time, count)
    if cooldown:
        st.error(
            f"相同请求刚刚执行过，请等待约 {cooldown} 秒后再试，防止重复计费。",
            icon=":material/hourglass_top:",
        )
    with st.container(horizontal=True):
        if st.button("取消", icon=":material/close:"):
            st.rerun()
        if st.button(
            "确认并获取",
            type="primary",
            icon=":material/check:",
            disabled=bool(cooldown),
        ):
            try:
                with st.spinner(f"正在获取 {count} 条视频并计算近 7 天热门榜单……"):
                    discovery_result = discovery_service.discover(
                        keyword=keyword,
                        adapter=douyin_adapter,
                        publish_time=publish_time,
                        count=count,
                    )
                    trend_service.recompute(keyword)
                st.session_state["last_discovery_result"] = discovery_result.model_dump(
                    mode="json"
                )
                st.session_state["candidate_local_query"] = keyword.strip()
                st.session_state["active_trend_keyword"] = keyword.strip()
                st.rerun()
            except Exception as exc:
                st.error(str(exc), icon=":material/error:")


with st.container(border=True):
    st.subheader("抖音热门视频获取")
    st.caption(
        "输入关键词后，从抖音综合排序获取 1–10 条视频，并根据近 7 天数据生成系统热门排名。"
        "每次确认只调用 1 次搜索接口，不自动翻页。"
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
            candidate_count = st.number_input(
                "获取数量",
                min_value=1,
                max_value=10,
                value=10,
                step=1,
                key="platform_candidate_count",
            )
            discover_submitted = st.form_submit_button(
                "获取热门视频（调用1次）",
                type="primary",
                icon=":material/travel_explore:",
                disabled=not douyin_capability.enabled,
            )
    if douyin_capability.enabled:
        st.success(
            "抖音接口凭证已配置；首次调用后由抖音返回实际权限状态。",
            icon=":material/key:",
        )
    else:
        st.warning(
            "尚未配置抖音接口凭证，当前不会发起任何平台请求。请联系技术人员配置。",
            icon=":material/key_off:",
        )
        with st.expander("技术配置说明"):
            st.code(
                'DOUYIN_CLIENT_KEY = ""\nDOUYIN_CLIENT_SECRET = ""',
                language="toml",
            )

    if discover_submitted:
        confirm_keyword_discovery(
            platform_keyword, publish_window_label, int(candidate_count)
        )

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
    checkpoints = (
        repository.list_sampling_checkpoints(trend_keyword) if trend_keyword else []
    )
    if checkpoints:
        status_labels = {
            "pending": "待复采",
            "observed": "已复采",
            "missed": "未再次召回",
        }
        with st.expander("复采计划与增长数据完整度"):
            st.caption("系统只提醒，不会自动调用付费接口。")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "视频": checkpoint.candidate_id,
                            "计划间隔": f"T+{checkpoint.offset_hours}小时",
                            "计划时间": checkpoint.due_at,
                            "状态": status_labels[checkpoint.status.value],
                        }
                        for checkpoint in checkpoints
                    ]
                ),
                hide_index=True,
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
                    "candidate_id": candidate.video_id,
                    "自有排名": own_rank,
                    "标题": candidate.title,
                    "热门程度": trend.level.value,
                    "趋势分": trend.score,
                    "数据可靠性": f"{trend.confidence:.0%}",
                    "为什么热门": "；".join(trend.reasons[:3]),
                }
            )
        st.markdown(f"**系统自有 Top 10 · {trend_keyword}**")
        trend_frame = pd.DataFrame(trend_rows)
        trend_event = st.dataframe(
            trend_frame,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="keyword_trend_table",
            column_config={
                "candidate_id": None,
                "趋势分": st.column_config.ProgressColumn(
                    "趋势分", min_value=0, max_value=100
                ),
            },
        )
        if trend_event.selection.rows:
            select_candidate(
                str(trend_frame.iloc[trend_event.selection.rows[0]]["candidate_id"])
            )
        with st.expander("查看计算依据"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "标题": candidate_lookup[item.candidate_id].title,
                            "平台位置": item.platform_rank,
                            "点赞增长/小时": item.like_growth_per_hour,
                            "近7天入榜次数": item.appearance_count,
                            "样本池": item.pool_size,
                            "异常状态": (
                                "待核验"
                                if item.anomaly_status.value == "suspected"
                                else "正常"
                            ),
                        }
                        for item in trend_results
                        if item.candidate_id in candidate_lookup
                    ]
                ),
                hide_index=True,
            )
        st.caption(
            "该排名由系统公式计算；平台综合位置仅是一个分量。样本池少于 30 条时只显示“观察中”。"
        )

st.subheader("历史视频库")
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
    select_candidate(None)
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
    return {
        "video_id": candidate.video_id,
        "标题": candidate.title,
        "发布时间": candidate.published_at,
        "热门判断": hotspot_status,
        "热度分": candidate.heat.score,
        "数据可靠性": f"{candidate.metrics.confidence:.0%}",
        "判断依据": "；".join(candidate.heat.reasons[:2]),
        "来源": str(candidate.source_url),
    }


with st.container(border=True):
    st.subheader("热点候选榜")
    st.caption("单选一个视频，查看爆火依据并转成文案。")
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
                "来源": st.column_config.LinkColumn(display_text="查看来源"),
            },
        )
        if event.selection.rows:
            select_candidate(str(dataframe.iloc[event.selection.rows[0]]["video_id"]))

visible_ids = {item.video_id for item in items}
selected = candidate_service.get(selected_candidate_id())
if (
    selected
    and selected.video_id not in visible_ids
    and not any(result.candidate_id == selected.video_id for result in trend_results)
):
    select_candidate(None)
    selected = None
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
                "当前为试运行判断；达到稳定样本与可靠性门槛后才转为正式结论。",
                icon=":material/warning:",
            )
        if st.button("将此视频转成文案", type="primary", icon=":material/transcribe:"):
            st.switch_page("app_pages/transcription.py")

st.divider()
with st.expander("备用导入与数据同步", icon=":material/database:"):
    render_backup_imports(source_service, repository)
