from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from src.adapters import (
    DouyinHotBillboardAdapter,
    DouyinKeywordAdapter,
    ManualImportAdapter,
    PublicMetadataResearchAdapter,
)
from src.app_state import (
    get_repository,
    get_services,
    get_source_service,
    select_candidate,
    selected_candidate_id,
)
from src.models import DataSource, HeatLevel, Platform, SourceRequest, VideoCandidate
from src.ui import render_heat_badge, render_page_header

render_page_header(
    "爆火视频检索",
    "在已导入或已授权的数据范围内发现、复核并追踪数字人口播候选。",
    icon="trending_up",
)

candidate_service, _, _ = get_services()
repository = get_repository()
source_service = get_source_service()
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
            "检索候选", type="primary", icon=":material/search:"
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
        if st.button(
            "进入音视频转文案", type="primary", icon=":material/arrow_forward:"
        ):
            st.switch_page("app_pages/transcription.py")

st.divider()
st.subheader("候选导入与数据源同步")
st.caption(
    "首期默认使用 CSV/Excel、手工链接和显式公开 URL。官方热门榜与关键词搜索在权限获批前保持关闭。"
)

source_label = st.selectbox(
    "数据源",
    [
        "CSV / Excel",
        "手工链接与指标",
        "显式抖音公开 URL",
        "抖音官方热门榜（未启用）",
        "抖音官方关键词（未启用）",
    ],
)

if source_label == "CSV / Excel":
    template = ManualImportAdapter.template().to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "下载导入模板",
        template,
        file_name="digital_human_candidates.csv",
        mime="text/csv",
        icon=":material/download:",
    )
    uploaded = st.file_uploader("上传候选或新一轮指标快照", type=["csv", "xlsx"])
    if uploaded is not None:
        adapter = ManualImportAdapter(uploaded.name, uploaded.getvalue())
        request = SourceRequest(
            source=DataSource.CSV,
            keywords=[
                "数字人口播",
                "数字人营销",
                "AI获客",
                "企业服务",
                "AI工具",
                "SaaS",
                "私域",
                "线索",
                "询盘",
            ],
        )
        page = adapter.sync(request)
        if page.items:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "作品ID": item.platform_item_id,
                            "标题": item.title,
                            "作者": item.author_name,
                            "发布时间": item.published_at,
                            "采样时间": item.metrics.sampled_at,
                            "播放": item.metrics.plays,
                            "点赞": item.metrics.likes,
                            "置信度": item.metrics.confidence,
                        }
                        for item in page.items
                    ]
                ),
                hide_index=True,
            )
        if page.errors:
            st.error(f"发现 {len(page.errors)} 个导入错误，错误行不会写入数据库。")
            st.dataframe(
                pd.DataFrame([error.model_dump() for error in page.errors]),
                hide_index=True,
            )
        if st.button("确认导入有效行", type="primary", disabled=not page.items):
            report = source_service.import_page(page)
            st.success(
                f"新增候选 {report.added_candidates}，更新候选 {report.updated_candidates}，"
                f"新增快照 {report.added_snapshots}，重复快照 {report.duplicates}。"
            )
            st.rerun()
elif source_label == "手工链接与指标":
    with st.form("manual_candidate"):
        manual_item_id = st.text_input("抖音作品 ID")
        manual_title = st.text_input("标题")
        manual_author = st.text_input("作者")
        manual_url = st.text_input("公开分享链接")
        published_date = st.date_input("发布时间日期")
        published_time = st.time_input("发布时间时间")
        with st.container(horizontal=True):
            manual_plays = st.number_input("播放", min_value=0, value=None)
            manual_likes = st.number_input("点赞", min_value=0, value=None)
            manual_comments = st.number_input("评论", min_value=0, value=None)
            manual_shares = st.number_input("分享", min_value=0, value=None)
            manual_favorites = st.number_input("收藏", min_value=0, value=None)
            manual_followers = st.number_input("作者粉丝", min_value=0, value=None)
        manual_evidence = st.text_input("证据说明或截图编号")
        manual_submit = st.form_submit_button("保存手工记录", type="primary")
    if manual_submit:
        page = ManualImportAdapter.from_manual(
            platform_item_id=manual_item_id,
            title=manual_title,
            author_name=manual_author,
            published_at=datetime.combine(published_date, published_time).astimezone(),
            source_url=manual_url,
            sampled_at=datetime.now().astimezone(),
            plays=manual_plays,
            likes=manual_likes,
            comments=manual_comments,
            shares=manual_shares,
            favorites=manual_favorites,
            followers=manual_followers,
            evidence=manual_evidence or None,
        )
        if page.items:
            report = source_service.import_page(page)
            st.success(
                f"已保存候选 {report.added_candidates + report.updated_candidates} 条，"
                f"新增快照 {report.added_snapshots} 条。"
            )
            st.rerun()
        else:
            st.error(page.errors[0].message if page.errors else "手工记录校验失败。")
elif source_label == "显式抖音公开 URL":
    public_url = st.text_input(
        "抖音公开分享链接", placeholder="https://www.douyin.com/video/..."
    )
    if st.button("读取公开元数据", type="primary", disabled=not public_url.strip()):
        try:
            request = SourceRequest(
                source=DataSource.PUBLIC_RESEARCH,
                urls=[public_url.strip()],
                keywords=[
                    "数字人口播",
                    "数字人营销",
                    "AI获客",
                    "企业服务",
                    "AI工具",
                    "SaaS",
                    "私域",
                    "线索",
                    "询盘",
                ],
            )
            page = PublicMetadataResearchAdapter().sync(request)
            if page.items:
                report = source_service.import_page(page)
                st.success(
                    f"已保存 {report.added_candidates + report.updated_candidates} 条公开元数据。"
                )
                st.rerun()
            else:
                st.error(
                    page.errors[0].message if page.errors else "没有读取到公开元数据。"
                )
        except Exception as exc:
            st.error(str(exc))
else:
    adapter = (
        DouyinHotBillboardAdapter()
        if "热门榜" in source_label
        else DouyinKeywordAdapter()
    )
    st.info("当前没有抖音正式权限，此入口不会发起网络请求。")
    if st.button("检查权限状态"):
        try:
            adapter.sync(SourceRequest(source=DataSource.OFFICIAL))
        except Exception as exc:
            st.error(str(exc))

st.subheader("人工复核队列")
pending_reviews = {review.candidate_id: review for review in repository.list_reviews()}
review_rows = []
for candidate in candidate_service.search(published_within_hours=720):
    review = pending_reviews.get(candidate.video_id)
    review_rows.append(
        {
            "候选ID": candidate.video_id,
            "标题": candidate.title,
            "状态": review.status.value if review else "pending",
            "复核人": review.reviewer if review else None,
            "排除原因": review.exclusion_reason if review else None,
        }
    )
st.dataframe(pd.DataFrame(review_rows), hide_index=True)

if selected:
    current_review = repository.get_review(selected.video_id)
    with st.form("relevance_review"):
        st.markdown(f"**当前复核：{selected.title}**")
        is_digital_human = st.checkbox(
            "数字人实际出镜",
            value=bool(current_review and current_review.is_digital_human),
        )
        is_target_vertical = st.checkbox(
            "属于 B2B/AI 企业服务",
            value=bool(current_review and current_review.is_target_vertical),
        )
        has_marketing_cta = st.checkbox(
            "存在营销获客 CTA",
            value=bool(current_review and current_review.has_marketing_cta),
        )
        reviewer = st.text_input(
            "复核人",
            value=(
                current_review.reviewer
                if current_review and current_review.reviewer
                else "运营复核员"
            ),
        )
        review_evidence = st.text_input(
            "证据说明",
            value=(
                current_review.evidence
                if current_review and current_review.evidence
                else ""
            ),
        )
        exclusion_reason = st.text_input(
            "排除原因（不满足时填写）",
            value=(
                current_review.exclusion_reason
                if current_review and current_review.exclusion_reason
                else ""
            ),
        )
        if st.form_submit_button("保存复核结果", type="primary"):
            result = source_service.review(
                selected.video_id,
                is_digital_human=is_digital_human,
                is_target_vertical=is_target_vertical,
                has_marketing_cta=has_marketing_cta,
                reviewer=reviewer,
                evidence=review_evidence or None,
                exclusion_reason=exclusion_reason or None,
            )
            st.success(f"复核结果已保存：{result.status.value}")
            st.rerun()

reports = repository.list_sync_reports()
if reports:
    st.subheader("最近同步任务")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "批次": report.run_id,
                    "数据源": report.source.value,
                    "完成时间": report.finished_at,
                    "新增候选": report.added_candidates,
                    "更新候选": report.updated_candidates,
                    "新增快照": report.added_snapshots,
                    "重复": report.duplicates,
                    "错误": len(report.errors),
                    "建议下次同步": report.next_suggested_sync_at,
                }
                for report in reports
            ]
        ),
        hide_index=True,
    )
