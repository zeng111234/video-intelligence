from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from uuid import uuid4

from src.contracts import CandidateRepository
from src.models import (
    EligibilityStatus,
    HeatLevel,
    HeatResult,
    NormalizedCandidate,
    RelevanceReview,
    ReviewStatus,
    SourcePage,
    SyncReport,
    VideoCandidate,
)
from src.services.heat import HeatService


class SourceService:
    def __init__(
        self, repository: CandidateRepository, heat_service: HeatService
    ) -> None:
        self.repository = repository
        self.heat_service = heat_service

    def import_page(self, page: SourcePage) -> SyncReport:
        started_at = datetime.now().astimezone()
        before = {
            (item.platform.value, item.platform_item_id or item.video_id): item.video_id
            for item in self.repository.list_candidates()
        }
        added = 0
        updated = 0
        duplicates = 0
        added_snapshots = 0
        missing_fields: Counter[str] = Counter()

        for normalized in page.items:
            key = (normalized.platform.value, normalized.platform_item_id)
            existing_id = before.get(key)
            video_id = (
                existing_id
                or f"{normalized.platform.value}-{normalized.platform_item_id}"
            )
            previous_times = {
                snapshot.sampled_at
                for snapshot in self.repository.list_snapshots(video_id)
            }
            candidate = self._to_candidate(normalized, video_id)
            saved_id = self.repository.save_candidate(candidate)
            if existing_id:
                updated += 1
            else:
                added += 1
                before[key] = saved_id
            if normalized.metrics.sampled_at in previous_times:
                duplicates += 1
            else:
                added_snapshots += 1
            for field in (
                "plays",
                "likes",
                "comments",
                "shares",
                "favorites",
                "followers",
            ):
                if getattr(normalized.metrics, field) is None:
                    missing_fields[field] += 1
        self.recompute_all()
        finished_at = datetime.now().astimezone()
        source = (
            page.items[0].source_type if page.items else self._fallback_source(page)
        )
        report = SyncReport(
            run_id=f"sync-{uuid4().hex[:10]}",
            source=source,
            started_at=started_at,
            finished_at=finished_at,
            added_candidates=added,
            updated_candidates=updated,
            added_snapshots=added_snapshots,
            duplicates=duplicates,
            missing_fields=dict(missing_fields),
            errors=page.errors,
            next_suggested_sync_at=(
                finished_at + timedelta(hours=2) if page.items else None
            ),
        )
        self.repository.save_sync_report(report)
        return report

    @staticmethod
    def _fallback_source(page: SourcePage):
        from src.models import DataSource

        return DataSource.MANUAL

    @staticmethod
    def _to_candidate(item: NormalizedCandidate, video_id: str) -> VideoCandidate:
        metrics = item.metrics.model_copy(update={"item_id": video_id})
        return VideoCandidate(
            video_id=video_id,
            platform_item_id=item.platform_item_id,
            title=item.title,
            author_id=item.author_id,
            author_name=item.author_name,
            platform=item.platform,
            category=item.category,
            published_at=item.published_at,
            source_url=item.source_url,
            source_type=item.source_type,
            rights_status=item.rights_status,
            matched_by=item.matched_by,
            cohort_key=item.cohort_key,
            eligibility_status=item.eligibility_status,
            evidence=item.evidence,
            feed_id=item.feed_id,
            finder_user_name=item.finder_user_name,
            official_hot=item.official_hot,
            official_rank=item.official_rank,
            official_hot_value=item.official_hot_value,
            metrics=metrics,
            heat=HeatResult(
                score=0,
                level=HeatLevel.INSUFFICIENT,
                confidence=metrics.confidence,
                reasons=["等待同桶热度计算"],
                model_version="rule-v1",
            ),
        )

    def recompute_all(self) -> None:
        candidates = self.repository.list_candidates()
        histories = {
            candidate.video_id: self.repository.list_snapshots(candidate.video_id)
            for candidate in candidates
        }
        for candidate in candidates:
            history = histories[candidate.video_id]
            if not history:
                continue
            heat = self.heat_service.analyze(
                history[-1],
                candidate=candidate,
                snapshots=history,
                peers=[
                    peer
                    for peer in candidates
                    if peer.cohort_key == candidate.cohort_key
                ],
                peer_snapshots=histories,
            )
            self.repository.save_candidate(candidate.model_copy(update={"heat": heat}))

    def review(
        self,
        candidate_id: str,
        *,
        is_digital_human: bool,
        is_target_vertical: bool,
        has_marketing_cta: bool,
        reviewer: str,
        evidence: str | None = None,
        exclusion_reason: str | None = None,
    ) -> RelevanceReview:
        approved = is_digital_human and is_target_vertical and has_marketing_cta
        review = RelevanceReview(
            candidate_id=candidate_id,
            is_digital_human=is_digital_human,
            is_target_vertical=is_target_vertical,
            has_marketing_cta=has_marketing_cta,
            status=ReviewStatus.APPROVED if approved else ReviewStatus.REJECTED,
            reviewer=reviewer.strip() or "运营复核员",
            reviewed_at=datetime.now().astimezone(),
            evidence=evidence,
            exclusion_reason=None
            if approved
            else (exclusion_reason or "不满足首期三个入选条件"),
        )
        self.repository.save_review(review)
        candidate = self.repository.get_candidate(candidate_id)
        if candidate is not None:
            self.repository.save_candidate(
                candidate.model_copy(
                    update={
                        "eligibility_status": (
                            EligibilityStatus.APPROVED
                            if approved
                            else EligibilityStatus.REJECTED
                        )
                    }
                )
            )
        self.recompute_all()
        return review
