from __future__ import annotations

from src.models import SourcePage, SourceRequest


class OfficialAdapterDisabledError(RuntimeError):
    pass


class _DisabledOfficialAdapter:
    permission_name = "官方数据权限"

    def sync(self, request: SourceRequest) -> SourcePage:
        raise OfficialAdapterDisabledError(
            f"{self.permission_name}尚未配置；请使用 CSV、手工链接或公开元数据研究入口。"
        )


class DouyinHotBillboardAdapter(_DisabledOfficialAdapter):
    permission_name = "data.external.billboard_hot_video"


class DouyinKeywordAdapter(_DisabledOfficialAdapter):
    permission_name = "video.search 与审核业务关键词"
