from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from src.adapters import (
    DouyinHotBillboardAdapter,
    ManualImportAdapter,
    PublicMetadataResearchAdapter,
)
from src.models import DataSource, SourceRequest
from src.platforms import SUPPORTED_PLATFORMS, platform_label


def render_backup_imports(source_service, repository) -> None:
    st.caption(
        "可补录抖音、小红书、微信视频号的公开链接或可追溯字段；不会自动抓取或下载媒体。"
    )
    source_label = st.selectbox(
        "备用数据源",
        [
            "CSV / Excel",
            "手工链接与指标",
            "显式抖音公开 URL",
            "抖音官方热门榜（未启用）",
        ],
    )

    if source_label == "CSV / Excel":
        template = (
            ManualImportAdapter.template().to_csv(index=False).encode("utf-8-sig")
        )
        st.download_button(
            "下载导入模板",
            template,
            file_name="multi_platform_candidates.csv",
            mime="text/csv",
            icon=":material/download:",
        )
        uploaded = st.file_uploader(
            "上传三平台候选或新一轮指标快照", type=["csv", "xlsx"]
        )
        if uploaded is not None:
            adapter = ManualImportAdapter(uploaded.name, uploaded.getvalue())
            page = adapter.sync(SourceRequest(source=DataSource.CSV))
            if page.items:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "平台": platform_label(item.platform),
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
        manual_platform = st.selectbox(
            "平台",
            SUPPORTED_PLATFORMS,
            format_func=platform_label,
            key="manual_platform",
        )
        with st.form("manual_candidate"):
            manual_item_id = st.text_input("作品 ID（视频号可填 feedId）")
            manual_title = st.text_input("标题")
            manual_author = st.text_input("作者")
            manual_url = st.text_input("公开分享链接（视频号可留空）")
            manual_feed_id = st.text_input("视频号 feedId")
            manual_finder_user_name = st.text_input("视频号 finderUserName")
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
                platform=manual_platform,
                title=manual_title,
                author_name=manual_author,
                published_at=datetime.combine(
                    published_date, published_time
                ).astimezone(),
                source_url=manual_url,
                sampled_at=datetime.now().astimezone(),
                plays=manual_plays,
                likes=manual_likes,
                comments=manual_comments,
                shares=manual_shares,
                favorites=manual_favorites,
                followers=manual_followers,
                evidence=manual_evidence or None,
                feed_id=manual_feed_id or None,
                finder_user_name=manual_finder_user_name or None,
            )
            if page.items:
                report = source_service.import_page(page)
                st.success(
                    f"已保存候选 {report.added_candidates + report.updated_candidates} 条，"
                    f"新增快照 {report.added_snapshots} 条。"
                )
                st.rerun()
            else:
                st.error(
                    page.errors[0].message if page.errors else "手工记录校验失败。"
                )
    elif source_label == "显式抖音公开 URL":
        public_url = st.text_input(
            "抖音公开分享链接", placeholder="https://www.douyin.com/video/..."
        )
        if st.button("读取公开元数据", type="primary", disabled=not public_url.strip()):
            try:
                page = PublicMetadataResearchAdapter().sync(
                    SourceRequest(
                        source=DataSource.PUBLIC_RESEARCH, urls=[public_url.strip()]
                    )
                )
                if page.items:
                    report = source_service.import_page(page)
                    st.success(
                        f"已保存 {report.added_candidates + report.updated_candidates} 条公开元数据。"
                    )
                    st.rerun()
                else:
                    st.error(
                        page.errors[0].message
                        if page.errors
                        else "没有读取到公开元数据。"
                    )
            except Exception as exc:
                st.error(str(exc))
    else:
        adapter = DouyinHotBillboardAdapter()
        st.info("当前没有抖音正式权限，此入口不会发起网络请求。")
        if st.button("检查权限状态"):
            try:
                adapter.sync(SourceRequest(source=DataSource.OFFICIAL))
            except Exception as exc:
                st.error(str(exc))

    reports = repository.list_sync_reports()
    if reports:
        st.subheader("最近备用导入任务")
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
                    }
                    for report in reports
                ]
            ),
            hide_index=True,
        )
