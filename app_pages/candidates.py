from __future__ import annotations

import pandas as pd
import streamlit as st

from src.app_state import (
    get_repository,
    get_services,
    get_source_service,
    select_candidate,
    selected_candidate_id,
)
from src.backup_import_ui import render_backup_imports
from src.config import build_licensed_search_provider
from src.models import (
    HeatLevel,
    KeywordTrendLevel,
    KeywordTrendResult,
    Platform,
    PlatformRunStatus,
    ProviderMode,
    VideoCandidate,
)
from src.platforms import SUPPORTED_PLATFORMS, platform_label
from src.services.commercial_search import (
    CommercialSearchService,
    MONTHLY_HARD_LIMIT_QUERIES,
    MONTHLY_WARNING_QUERIES,
)
from src.services.keyword_trend import KeywordTrendService
from src.ui import render_page_header

render_page_header(
    "爆火视频检索",
    "输入关键词，一次查看抖音、小红书和微信视频号的独立爆火候选榜。",
    icon="trending_up",
)

candidate_service, _, _ = get_services()
repository = get_repository()
source_service = get_source_service()
provider = build_licensed_search_provider(st.secrets)
provider_capability = provider.capabilities()
commercial_service = CommercialSearchService(
    repository,
    source_service,
    KeywordTrendService(repository),
    provider,
)

RUN_STATUS_LABELS = {
    PlatformRunStatus.QUEUED: "等待执行",
    PlatformRunStatus.RUNNING: "正在获取",
    PlatformRunStatus.SUCCEEDED: "获取完成",
    PlatformRunStatus.CACHED: "使用10分钟缓存",
    PlatformRunStatus.FAILED: "获取失败",
    PlatformRunStatus.BLOCKED: "已阻止调用",
    PlatformRunStatus.OUTCOME_UNKNOWN: "费用状态待核对",
}


def candidate_status_label(trend: KeywordTrendResult) -> str:
    if trend.level != KeywordTrendLevel.OBSERVING:
        return trend.level.value
    if trend.score >= 75:
        return "高潜候选"
    if trend.score >= 60:
        return "值得关注"
    return "一般候选"


def reliability_label(confidence: float) -> str:
    if confidence < 0.60:
        return f"初次观测（{confidence:.0%}）"
    if confidence < 0.80:
        return f"持续观察（{confidence:.0%}）"
    return f"数据较充分（{confidence:.0%}）"


def display_reasons(trend: KeywordTrendResult) -> list[str]:
    reasons = [f"本次平台搜索排第 {trend.platform_rank} 名（共 {trend.pool_size} 条）"]
    engagement_percentile = trend.percentiles.get("age_adjusted_engagement")
    if engagement_percentile is not None:
        leading_percent = max(1, round(100 - engagement_percentile))
        reasons.append(f"按发布时间折算的互动速度位于本平台候选前 {leading_percent}%")
    reasons.append(f"近 7 天有效入榜 {trend.appearance_count} 次")
    if trend.like_growth_per_hour is None:
        reasons.append("这是首次观测；至少 2 小时后再次查询才能判断增长")
    else:
        reasons.append(f"点赞增长约 {trend.like_growth_per_hour:.1f} 次/小时")
    if trend.pool_size < 30:
        reasons.append(f"当前仅有 {trend.pool_size} 条同平台样本，暂不输出正式爆火等级")
    return reasons


def run_error_message(platform: Platform, error: str | None) -> str:
    detail = error or "未返回明确原因"
    if platform == Platform.WECHAT_CHANNELS and (
        "Request failed" in detail or "parameters are correct" in detail
    ):
        return "供应商拒绝了旧版视频号时间参数；参数已修正，下次查询生效。"
    return detail


@st.dialog("确认三平台查询")
def confirm_three_platform_search(
    keyword: str,
    published_window_label: str,
    count: int,
    force_refresh: bool,
) -> None:
    published_window_days = 1 if published_window_label == "近 24 小时" else 7
    try:
        previews = commercial_service.preview(
            keyword=keyword,
            published_window_days=published_window_days,
            count=count,
            force_refresh=force_refresh,
        )
    except ValueError as exc:
        st.error(str(exc), icon=":material/error:")
        return

    st.write(f"关键词：**{keyword.strip() or '未填写'}**")
    st.write(f"时间范围：**{published_window_label}**")
    st.write(f"每个平台：**最多 {count} 条**")
    preview_rows = []
    for preview in previews:
        if preview.blocked_reason:
            source_status = preview.blocked_reason
        elif preview.cache_hit:
            source_status = "读取10分钟缓存"
        elif provider_capability.mode == ProviderMode.SANDBOX:
            source_status = "生成离线演示数据"
        else:
            source_status = "调用商业数据接口"
        preview_rows.append(
            {
                "平台": platform_label(preview.platform),
                "本次数据来源": source_status,
                "预计新增接口调用": preview.estimated_api_calls,
            }
        )
    st.dataframe(pd.DataFrame(preview_rows), hide_index=True)
    estimated_calls = sum(item.estimated_api_calls for item in previews)
    blocked = any(item.blocked_reason for item in previews)
    if provider_capability.mode == ProviderMode.SANDBOX:
        st.info(
            "当前为演示模式，不访问三个平台，也不会产生商业接口费用。",
            icon=":material/science:",
        )
    else:
        st.warning(
            f"本次预计新增 {estimated_calls} 次平台查询；实际费用以供应商账户结算，系统不会显示为免费。",
            icon=":material/paid:",
        )
    if force_refresh:
        st.warning(
            "已选择强制刷新，将忽略10分钟缓存；生产模式下可能产生新费用。",
            icon=":material/refresh:",
        )
    if blocked:
        st.error("当前存在不可执行的平台，请联系技术人员检查数据授权。")

    with st.container(horizontal=True):
        if st.button("取消", icon=":material/close:"):
            st.rerun()
        if st.button(
            "确认并查询",
            type="primary",
            icon=":material/check:",
            disabled=blocked,
        ):
            with st.spinner("正在分别获取三个平台的候选并计算榜单……"):
                batch = commercial_service.execute(
                    keyword=keyword,
                    published_window_days=published_window_days,
                    count=count,
                    force_refresh=force_refresh,
                )
            st.session_state["active_search_batch_id"] = batch.batch_id
            st.session_state["active_trend_keyword"] = batch.keyword
            st.session_state["candidate_local_query"] = batch.keyword
            select_candidate(None)
            st.rerun()


with st.container(border=True):
    st.subheader("三平台关键词爆款榜")
    st.caption("每个平台独立排名，不把抖音、小红书和视频号的数据混在一起比较。")
    if provider_capability.mode == ProviderMode.SANDBOX:
        st.warning(
            "当前显示演示数据，用于验证完整操作流程；尚未接入新榜或数说故事真实接口。",
            icon=":material/science:",
        )
    elif not provider_capability.enabled:
        st.error(
            "真实商业接口尚未配置完成，系统不会发起平台请求。请在密钥到位后由技术人员启用。",
            icon=":material/key_off:",
        )
    elif provider_capability.provider_name == "oneapi":
        st.warning(
            "当前为 OneAPI 小流量试点。接口字段和商业展示授权尚待真实验收，建议先用一个关键词核对结果。",
            icon=":material/experiment:",
        )

    with st.form("three_platform_search", enter_to_submit=False):
        with st.container(horizontal=True, vertical_alignment="bottom"):
            keyword = st.text_input(
                "关键词",
                placeholder="例如：二手车",
                key="three_platform_keyword_input",
            )
            published_window_label = st.selectbox(
                "时间范围",
                ["近 7 天", "近 24 小时"],
                key="three_platform_publish_window",
            )
            count = st.number_input(
                "每平台数量",
                min_value=1,
                max_value=10,
                value=10,
                step=1,
                key="three_platform_count",
            )
            submitted = st.form_submit_button(
                (
                    "三平台一键查爆款（演示）"
                    if provider_capability.mode == ProviderMode.SANDBOX
                    else "三平台一键查爆款"
                ),
                type="primary",
                icon=":material/travel_explore:",
                disabled=not provider_capability.enabled,
            )
        force_refresh = st.toggle(
            "强制刷新（可能产生新费用）",
            value=False,
            help="默认优先读取10分钟内的成功结果。仅在确实需要更新时开启。",
        )

    if submitted:
        confirm_three_platform_search(
            keyword,
            published_window_label,
            int(count),
            force_refresh,
        )

    monthly_queries = commercial_service.monthly_query_count()
    with st.container(horizontal=True):
        st.metric(
            "数据模式",
            "演示数据"
            if provider_capability.mode == ProviderMode.SANDBOX
            else (
                "OneAPI 试点"
                if provider_capability.provider_name == "oneapi"
                else "商业接口"
            ),
            border=True,
        )
        st.metric("本月平台查询", f"{monthly_queries} / 450", border=True)
        st.metric(
            "剩余安全额度",
            max(0, MONTHLY_HARD_LIMIT_QUERIES - monthly_queries),
            border=True,
        )
        st.metric("默认缓存", "10分钟", border=True)
    if monthly_queries >= MONTHLY_HARD_LIMIT_QUERIES:
        st.error("本月平台查询已达到硬上限，新的真实调用已停止。")
    elif monthly_queries >= MONTHLY_WARNING_QUERIES:
        st.warning("本月平台查询已超过80%，请留意供应商账单与剩余额度。")

    with st.expander("数据接入状态"):
        st.write(f"当前数据提供方：**{provider_capability.display_name}**")
        st.write(
            "支持平台："
            + "、".join(
                platform_label(item) for item in provider_capability.supported_platforms
            )
            if provider_capability.supported_platforms
            else "支持平台：尚未启用"
        )
        if provider_capability.missing_configuration:
            st.caption(
                "仍需完成：" + "、".join(provider_capability.missing_configuration)
            )


active_batch_id = st.session_state.get("active_search_batch_id")
active_batch = repository.get_search_batch(active_batch_id) if active_batch_id else None
if active_batch is None:
    recent_batches = repository.list_search_batches(limit=1)
    active_batch = recent_batches[0] if recent_batches else None

active_trend_results = []
if active_batch:
    runs = repository.list_platform_search_runs(active_batch.batch_id)
    run_by_platform = {run.platform: run for run in runs}
    with st.container(horizontal=True):
        st.metric("最近关键词", active_batch.keyword, border=True)
        st.metric(
            "三平台状态",
            {
                "succeeded": "全部完成",
                "partial": "部分完成",
                "failed": "全部失败",
            }.get(active_batch.status.value, "处理中"),
            border=True,
        )
        st.metric(
            "本批新增调用",
            sum(run.api_call_count for run in runs),
            border=True,
        )
        st.metric(
            "缓存命中",
            sum(run.cache_hit for run in runs),
            border=True,
        )

    st.subheader(f"{active_batch.keyword} · 三平台独立 Top 10")
    tabs = st.tabs([platform_label(platform) for platform in SUPPORTED_PLATFORMS])
    candidate_lookup = {
        candidate.video_id: candidate for candidate in repository.list_candidates()
    }
    for tab, platform in zip(tabs, SUPPORTED_PLATFORMS, strict=True):
        with tab:
            run = run_by_platform.get(platform)
            if run is None:
                st.info("该平台尚未执行。")
                continue
            status_label = RUN_STATUS_LABELS[run.status]
            if run.status in {
                PlatformRunStatus.FAILED,
                PlatformRunStatus.BLOCKED,
                PlatformRunStatus.OUTCOME_UNKNOWN,
            }:
                st.error(f"{status_label}：{run_error_message(platform, run.error)}")
                continue
            st.caption(
                f"{status_label} · 返回 {run.returned_count} 条 · "
                f"新增接口调用 {run.api_call_count} 次"
            )
            trends = repository.list_keyword_trend_results(
                active_batch.keyword,
                limit=10,
                platform=platform,
            )
            active_trend_results.extend(trends)
            trend_rows = []
            for own_rank, trend in enumerate(trends, start=1):
                candidate = candidate_lookup.get(trend.candidate_id)
                if candidate is None:
                    continue
                reasons = display_reasons(trend)
                data_source = (
                    "商业接口演示数据"
                    if active_batch.mode == ProviderMode.SANDBOX
                    else provider_capability.display_name
                )
                trend_rows.append(
                    {
                        "candidate_id": candidate.video_id,
                        "系统排名": own_rank,
                        "标题": candidate.title,
                        "发布时间": candidate.published_at,
                        "判断状态": candidate_status_label(trend),
                        "候选热度": trend.score,
                        "数据可靠性": reliability_label(trend.confidence),
                        "数据时间": candidate.metrics.sampled_at,
                        "数据来源": data_source,
                        "原始作品": (
                            str(candidate.source_url) if candidate.source_url else None
                        ),
                        "为什么入榜": "；".join(reasons[:3]),
                    }
                )
            if not trend_rows:
                if run.errors:
                    st.warning(
                        f"供应商返回内容已收到，但有 {len(run.errors)} 条因字段结构不匹配未入库。"
                        "解析兼容已修复，请在下次已确认查询时验证。"
                    )
                    with st.expander("查看本次字段诊断"):
                        for error in run.errors[:10]:
                            st.write(f"- {error.message}")
                else:
                    st.info("该平台本次没有符合条件的候选，不会用演示数据补齐。")
                continue
            trend_frame = pd.DataFrame(trend_rows)
            event = st.dataframe(
                trend_frame,
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key=f"commercial_trend_{platform.value}",
                column_config={
                    "candidate_id": None,
                    "标题": st.column_config.TextColumn(pinned=True),
                    "发布时间": st.column_config.DatetimeColumn(format="MM-DD HH:mm"),
                    "候选热度": st.column_config.ProgressColumn(
                        min_value=0,
                        max_value=100,
                        format="%.1f",
                    ),
                    "数据时间": st.column_config.DatetimeColumn(format="MM-DD HH:mm"),
                    "原始作品": st.column_config.LinkColumn(
                        "原始作品", display_text="查看原视频"
                    ),
                },
            )
            if event.selection.rows:
                selected_row = trend_frame.iloc[event.selection.rows[0]]
                select_candidate(str(selected_row["candidate_id"]))
            with st.expander("查看数据依据"):
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "标题": candidate_lookup[item.candidate_id].title,
                                "供应商召回位置": item.platform_rank,
                                "年龄归一化互动": item.engagement_per_hour,
                                "点赞增长/小时": item.like_growth_per_hour,
                                "近7天有效入榜": item.appearance_count,
                                "平台样本池": item.pool_size,
                                "异常状态": (
                                    "待核验"
                                    if item.anomaly_status.value == "suspected"
                                    else "正常"
                                ),
                            }
                            for item in trends
                            if item.candidate_id in candidate_lookup
                        ]
                    ),
                    hide_index=True,
                )
            st.caption(
                "高潜候选/值得关注是本次平台内的相对排序，不是正式爆火等级；"
                "至少间隔2小时复采后才能判断增长。"
            )


selected = candidate_service.get(selected_candidate_id())
selected_trend = next(
    (
        trend
        for trend in active_trend_results
        if selected and trend.candidate_id == selected.video_id
    ),
    None,
)
if selected:
    with st.container(border=True):
        st.subheader(selected.title)
        st.caption(
            f"{platform_label(selected.platform)} · {selected.author_name} · "
            + reliability_label(
                selected_trend.confidence
                if selected_trend
                else selected.metrics.confidence
            )
        )
        reasons = (
            display_reasons(selected_trend) if selected_trend else selected.heat.reasons
        )
        for reason in reasons:
            st.markdown(f"- {reason}")
        if selected.source_url:
            st.link_button("查看平台来源", str(selected.source_url))
        st.warning(
            "第一页接口未就绪时也可直接进入下一页体验。系统只接受授权上传，"
            "或直接返回 MP4/MOV 的 HTTPS 视频直链；不下载平台分享页，只提取视频音轨。",
            icon=":material/gavel:",
        )
        if st.button(
            "将此视频转成文案",
            type="primary",
            icon=":material/transcribe:",
        ):
            st.switch_page("app_pages/transcription.py")


def to_history_row(candidate: VideoCandidate) -> dict[str, object]:
    hotspot_status = {
        HeatLevel.S: "S级",
        HeatLevel.A: "A级",
        HeatLevel.B: "B级",
        HeatLevel.STATIC_HIGH: "静态高热",
        HeatLevel.ANOMALOUS: "异常待核验",
        HeatLevel.NORMAL: "普通",
        HeatLevel.INSUFFICIENT: "数据不足",
    }[candidate.heat.level]
    return {
        "video_id": candidate.video_id,
        "平台": platform_label(candidate.platform),
        "标题": candidate.title,
        "发布时间": candidate.published_at,
        "历史判断": hotspot_status,
        "数据可靠性": candidate.metrics.confidence,
        "来源": str(candidate.source_url) if candidate.source_url else None,
    }


with st.expander("历史视频库", icon=":material/history:"):
    all_candidates = candidate_service.search(published_within_hours=720)
    categories = ["全部赛道", *sorted({item.category for item in all_candidates})]
    with st.form("candidate_history_filters"):
        with st.container(horizontal=True, vertical_alignment="bottom"):
            history_query = st.text_input(
                "历史关键词",
                placeholder="例如：二手车",
                key="candidate_local_query",
            )
            selected_platforms = st.multiselect(
                "历史平台",
                SUPPORTED_PLATFORMS,
                default=list(SUPPORTED_PLATFORMS),
                format_func=platform_label,
            )
            category = st.selectbox("历史赛道", categories)
            history_submitted = st.form_submit_button(
                "检索历史视频",
                icon=":material/search:",
            )
    history_items = candidate_service.search(
        query=history_query,
        platforms=selected_platforms,
        category=category,
        published_within_hours=720,
    )
    if history_submitted:
        select_candidate(None)
    history_frame = pd.DataFrame([to_history_row(item) for item in history_items])
    if history_frame.empty:
        st.info("历史视频库暂无匹配内容。")
    else:
        history_event = st.dataframe(
            history_frame,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="candidate_history_table",
            column_config={
                "video_id": None,
                "标题": st.column_config.TextColumn(pinned=True),
                "发布时间": st.column_config.DatetimeColumn(format="MM-DD HH:mm"),
                "数据可靠性": st.column_config.NumberColumn(format="percent"),
                "来源": st.column_config.LinkColumn(display_text="查看来源"),
            },
        )
        if history_event.selection.rows:
            selected_row = history_frame.iloc[history_event.selection.rows[0]]
            select_candidate(str(selected_row["video_id"]))

with st.expander("备用导入与数据同步", icon=":material/database:"):
    render_backup_imports(source_service, repository)
