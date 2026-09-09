from __future__ import annotations

import os
from datetime import datetime, timedelta
from decimal import Decimal

from src.mock_data import build_mock_candidates, build_mock_tasks
from src.models import (
    CandidateMatch,
    CandidateCopyProbe,
    CrawlerKeywordQueue,
    DiscoveryResult,
    HotWordRecord,
    KeywordTrendResult,
    MediaResolutionAttempt,
    MediaResolutionStatus,
    PipelineRun,
    ProductionBatch,
    Platform,
    PlatformSearchRun,
    ProviderSafetyState,
    PublishSafetyState,
    AdminAccount,
    CustomerCode,
    RelevanceReview,
    SamplingCheckpoint,
    SearchBatch,
    SyncReport,
    TaskRecord,
    TaskStatus,
    TranscriptRevision,
    VideoCandidate,
    VideoMetricSnapshot,
)


class MockRepository:
    def __init__(
        self,
        candidates: list[VideoCandidate] | None = None,
        tasks: list[TaskRecord] | None = None,
    ) -> None:
        initial_candidates = (
            build_mock_candidates() if candidates is None else candidates
        )
        initial_tasks = build_mock_tasks() if tasks is None else tasks
        self._candidates = {item.video_id: item for item in initial_candidates}
        self._tasks = {item.task_id: item for item in initial_tasks}
        self._snapshots = {
            item.video_id: [item.metrics.model_copy(update={"item_id": item.video_id})]
            for item in initial_candidates
        }
        self._reviews: dict[str, RelevanceReview] = {}
        self._candidate_copy_probes: dict[str, CandidateCopyProbe] = {}
        self._sync_reports: dict[str, SyncReport] = {}
        self._discovery_results: dict[str, DiscoveryResult] = {}
        self._candidate_matches: dict[tuple[str, str], CandidateMatch] = {}
        self._keyword_trends: dict[tuple[str, Platform], list[KeywordTrendResult]] = {}
        self._transcript_revisions: dict[str, TranscriptRevision] = {}
        self._sampling_checkpoints: dict[str, SamplingCheckpoint] = {}
        self._discovery_request_guards: dict[str, tuple[str, datetime]] = {}
        self._search_batches: dict[str, SearchBatch] = {}
        self._crawler_keyword_queues: dict[str, CrawlerKeywordQueue] = {}
        self._platform_search_runs: dict[str, PlatformSearchRun] = {}
        self._provider_request_guards: dict[str, tuple[str, datetime, str]] = {}
        self._provider_safety_states: dict[str, ProviderSafetyState] = {}
        self._publish_safety_states: dict[tuple[str, str], PublishSafetyState] = {}
        self._media_resolution_attempts: dict[str, MediaResolutionAttempt] = {}
        self._media_resolution_guards: dict[str, tuple[str, str, datetime, str]] = {}
        self._pipeline_runs: dict[str, PipelineRun] = {}
        self._production_batches: dict[str, ProductionBatch] = {}
        self._production_operations: dict[tuple[str, str], dict] = {}
        self._video_editor_batches = {}
        self._video_editor_quotes: dict[str, dict] = {}
        self._video_editor_operations: dict[str, dict] = {}
        self._video_editor_cloud_jobs: dict[str, dict] = {}
        self._hot_words: dict[tuple[str, datetime], HotWordRecord] = {}
        # 演示仓库默认预置演示余额，避免测试/沙箱环境处处充值；生产走 SQLite 真实余额。
        self._credit_balance = Decimal(os.getenv("MOCK_CREDIT_BALANCE", "1000"))
        self._credit_transactions: list[dict] = []
        # 多账户：owner -> balance；默认账户与演示余额一致
        self._credit_accounts: dict[str, Decimal] = {"admin": self._credit_balance}
        self._customer_codes: dict[str, CustomerCode] = {}
        self._admin_accounts: dict[str, AdminAccount] = {}
        self._pricing: dict[str, str] = {}

    def get_credit_balance(self, owner: str = "admin") -> Decimal:
        return self._credit_accounts.get(
            owner, self._credit_balance if owner == "admin" else Decimal("0")
        )

    def ensure_credit_account(self, owner: str) -> Decimal:
        if owner not in self._credit_accounts:
            self._credit_accounts[owner] = Decimal("0")
        return self._credit_accounts[owner]

    def list_credit_transactions(
        self, owner: str = "admin", limit: int = 100
    ) -> list[dict]:
        filtered = [t for t in self._credit_transactions if t.get("owner") == owner]
        return list(filtered)[-int(limit) :][::-1]

    def adjust_credit_balance(
        self,
        *,
        amount,
        reason: str,
        owner: str = "admin",
        ref_type: str | None = None,
        ref_id: str | None = None,
        now: datetime | None = None,
    ) -> Decimal:
        amount = Decimal(str(amount))
        current = self._credit_accounts.get(owner, Decimal("0"))
        new_balance = current + amount
        if new_balance < 0:
            raise ValueError("积分不足")
        self._credit_accounts[owner] = new_balance
        self._credit_transactions.append(
            {
                "owner": owner,
                "amount": str(amount),
                "balance_after": str(new_balance),
                "reason": reason,
                "ref_type": ref_type,
                "ref_id": ref_id,
                "created_at": (now or datetime.now().astimezone()).isoformat(),
            }
        )
        return new_balance

    # ------------------------------------------------------------------
    # 客户激活码
    # ------------------------------------------------------------------

    def create_customer_codes(self, codes: list[CustomerCode]) -> None:
        for code in codes:
            self._customer_codes[code.code] = code

    def get_customer_code(self, code: str) -> CustomerCode | None:
        return self._customer_codes.get(code)

    def list_customer_codes(self) -> list[CustomerCode]:
        return sorted(
            self._customer_codes.values(), key=lambda c: c.created_at, reverse=True
        )

    def set_customer_code_enabled(self, code: str, enabled: bool) -> None:
        found = self._customer_codes.get(code)
        if found is not None:
            self._customer_codes[code] = found.model_copy(
                update={"enabled": enabled, "updated_at": datetime.now().astimezone()}
            )

    def activate_customer_code(
        self, code: str, activated_at: datetime
    ) -> CustomerCode | None:
        found = self._customer_codes.get(code)
        if found is None or found.valid_days is None or found.activated_at:
            return found
        updated = found.model_copy(
            update={
                "activated_at": activated_at,
                "access_expires_at": activated_at + timedelta(days=found.valid_days),
                "updated_at": activated_at,
            }
        )
        self._customer_codes[code] = updated
        return updated

    def extend_customer_code_access(
        self, code: str, days: int, package_price_credits: Decimal, now: datetime
    ) -> CustomerCode | None:
        found = self._customer_codes.get(code)
        if found is None or found.valid_days is None:
            return found
        expires_at = found.access_expires_at
        if found.activated_at is not None:
            base = expires_at if expires_at and expires_at > now else now
            expires_at = base + timedelta(days=days)
        updated = found.model_copy(
            update={
                "valid_days": days,
                "package_price_credits": package_price_credits,
                "access_expires_at": expires_at,
                "updated_at": now,
            }
        )
        self._customer_codes[code] = updated
        return updated

    def delete_customer_code(self, code: str) -> None:
        self._customer_codes.pop(code, None)

    # ------------------------------------------------------------------
    # 定价设置
    # ------------------------------------------------------------------

    def get_pricing(self, key: str) -> str | None:
        return self._pricing.get(key)

    def set_pricing(self, key: str, value: str) -> None:
        self._pricing[key] = value

    def list_pricing(self) -> list[dict]:
        return [
            {"key": key, "value": value, "updated_at": ""}
            for key, value in self._pricing.items()
        ]

    # ------------------------------------------------------------------
    # 管理员账号（多账号）
    # ------------------------------------------------------------------

    def create_admin_account(self, account: AdminAccount) -> None:
        self._admin_accounts[account.username] = account

    def get_admin_account(self, username: str) -> AdminAccount | None:
        return self._admin_accounts.get(username)

    def list_admin_accounts(self) -> list[AdminAccount]:
        return list(self._admin_accounts.values())

    def set_admin_password(self, username: str, password_hash: str) -> None:
        found = self._admin_accounts.get(username)
        if found is not None:
            self._admin_accounts[username] = found.model_copy(
                update={
                    "password_hash": password_hash,
                    "updated_at": datetime.now().astimezone(),
                }
            )

    def delete_admin_account(self, username: str) -> bool:
        return self._admin_accounts.pop(username, None) is not None

    def list_candidates(self) -> list[VideoCandidate]:
        return list(self._candidates.values())

    def get_candidates_by_ids(self, video_ids: list[str]) -> list[VideoCandidate]:
        """Return the requested candidates in one repository operation.

        The SQLite implementation uses this bulk method while assembling a
        crawler batch.  Keep the in-memory test repository on the same
        contract so batch rendering is exercised in tests rather than failing
        before the response is built.
        """
        return [
            self._candidates[video_id]
            for video_id in video_ids
            if video_id in self._candidates
        ]

    def list_official_hot_pool(
        self, platform: Platform = Platform.DOUYIN
    ) -> list[VideoCandidate]:
        pool = [
            candidate
            for candidate in self._candidates.values()
            if candidate.platform == platform
            and (
                candidate.official_hot
                or (candidate.evidence or "").startswith("official_billboard:")
            )
        ]
        return sorted(
            pool,
            key=lambda item: (
                item.official_rank is None,
                item.official_rank or 0,
            ),
        )

    def save_hot_words(self, words: list[HotWordRecord]) -> None:
        for item in words:
            self._hot_words[(item.word, item.fetched_at)] = item

    def list_hot_words(self, limit: int = 50) -> list[HotWordRecord]:
        latest: dict[str, HotWordRecord] = {}
        for item in self._hot_words.values():
            previous = latest.get(item.word)
            if previous is None or item.fetched_at > previous.fetched_at:
                latest[item.word] = item
        ordered = sorted(
            latest.values(),
            key=lambda item: (
                item.hot_value is None,
                -(item.hot_value or 0),
                -item.fetched_at.timestamp(),
            ),
        )
        return ordered[:limit]

    def get_candidate(self, video_id: str) -> VideoCandidate | None:
        return self._candidates.get(video_id)

    def resolve_candidate_id(self, platform: str, platform_item_id: str) -> str | None:
        for candidate in self._candidates.values():
            candidate_platform = (
                candidate.platform.value
                if isinstance(candidate.platform, Platform)
                else str(candidate.platform)
            )
            if (
                candidate_platform == platform
                and candidate.platform_item_id == platform_item_id
            ):
                return candidate.video_id
        return None

    def save_candidate_copy_probe(self, probe: CandidateCopyProbe) -> None:
        self._candidate_copy_probes[probe.candidate_id] = probe

    def get_candidate_copy_probe(self, candidate_id: str) -> CandidateCopyProbe | None:
        return self._candidate_copy_probes.get(candidate_id)

    def get_candidate_copy_probes_by_ids(
        self, candidate_ids: list[str]
    ) -> dict[str, CandidateCopyProbe]:
        """Bulk counterpart of ``get_candidate_copy_probe`` for batch views."""
        return {
            candidate_id: self._candidate_copy_probes[candidate_id]
            for candidate_id in candidate_ids
            if candidate_id in self._candidate_copy_probes
        }

    def save_candidate(self, candidate: VideoCandidate) -> str:
        self._candidates[candidate.video_id] = candidate
        self.append_snapshot(
            candidate.metrics.model_copy(update={"item_id": candidate.video_id})
        )
        return candidate.video_id

    def list_snapshots(self, item_id: str) -> list[VideoMetricSnapshot]:
        return sorted(
            self._snapshots.get(item_id, []), key=lambda item: item.sampled_at
        )

    def append_snapshot(self, snapshot: VideoMetricSnapshot) -> bool:
        snapshots = self._snapshots.setdefault(snapshot.item_id, [])
        if any(item.sampled_at == snapshot.sampled_at for item in snapshots):
            return False
        snapshots.append(snapshot)
        return True

    def save_review(self, review: RelevanceReview) -> None:
        self._reviews[review.candidate_id] = review

    def get_review(self, candidate_id: str) -> RelevanceReview | None:
        return self._reviews.get(candidate_id)

    def list_reviews(self) -> list[RelevanceReview]:
        return list(self._reviews.values())

    def save_sync_report(self, report: SyncReport) -> None:
        self._sync_reports[report.run_id] = report

    def list_sync_reports(self, limit: int = 20) -> list[SyncReport]:
        return sorted(
            self._sync_reports.values(), key=lambda item: item.finished_at, reverse=True
        )[:limit]

    def save_discovery_result(self, result: DiscoveryResult) -> None:
        self._discovery_results[result.request_id] = result

    def list_discovery_results(self, limit: int = 20) -> list[DiscoveryResult]:
        return sorted(
            self._discovery_results.values(),
            key=lambda item: item.finished_at,
            reverse=True,
        )[:limit]

    def claim_discovery_request(
        self,
        fingerprint: str,
        request_id: str,
        claimed_at: datetime,
        ttl_seconds: int = 60,
    ) -> bool:
        previous = self._discovery_request_guards.get(fingerprint)
        if previous and (claimed_at - previous[1]).total_seconds() < ttl_seconds:
            return False
        self._discovery_request_guards[fingerprint] = (request_id, claimed_at)
        return True

    def save_candidate_match(self, match: CandidateMatch) -> None:
        self._candidate_matches[(match.request_id, match.video_id)] = match

    def list_candidate_matches(self, request_id: str) -> list[CandidateMatch]:
        return [
            match
            for (stored_request_id, _), match in self._candidate_matches.items()
            if stored_request_id == request_id
        ]

    def list_candidate_matches_by_run_ids(
        self, run_ids: list[str]
    ) -> dict[str, list[CandidateMatch]]:
        """Bulk counterpart of ``list_candidate_matches`` used by batch views."""
        if not run_ids:
            return {}
        requested = set(run_ids)
        grouped: dict[str, list[CandidateMatch]] = {}
        for (request_id, _), match in self._candidate_matches.items():
            if request_id in requested:
                grouped.setdefault(request_id, []).append(match)
        return grouped

    def list_keyword_matches(
        self,
        keyword: str,
        since,
        platform: Platform = Platform.DOUYIN,
        provider_name: str | None = None,
    ) -> list[CandidateMatch]:
        normalized = keyword.casefold()
        return [
            match
            for match in self._candidate_matches.values()
            if match.keyword.casefold() == normalized
            and match.observed_at >= since
            and match.platform == platform
            and (provider_name is None or match.provider_name == provider_name)
        ]

    def save_keyword_trend_results(self, results: list[KeywordTrendResult]) -> None:
        if results:
            key = (results[0].keyword.casefold(), results[0].platform)
            self._keyword_trends[key] = list(results)

    def clear_keyword_trend_results(
        self, keyword: str, platform: Platform = Platform.DOUYIN
    ) -> None:
        self._keyword_trends.pop((keyword.casefold(), platform), None)

    def list_keyword_trend_results(
        self,
        keyword: str,
        limit: int = 10,
        platform: Platform = Platform.DOUYIN,
    ) -> list[KeywordTrendResult]:
        return sorted(
            self._keyword_trends.get((keyword.casefold(), platform), []),
            key=lambda item: (-item.score, item.platform_rank),
        )[:limit]

    def list_tasks(self, candidate_ids: list[str] | None = None) -> list[TaskRecord]:
        tasks = list(self._tasks.values())
        if candidate_ids:
            requested = set(candidate_ids)
            tasks = [
                task
                for task in tasks
                if getattr(task, "candidate_id", None) in requested
            ]
        return sorted(tasks, key=lambda task: task.created_at, reverse=True)

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)

    def save_task(self, task: TaskRecord) -> None:
        if task.status in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        } and task.finished_at is None:
            task = task.model_copy(update={"finished_at": task.updated_at})
        self._tasks[task.task_id] = task

    def delete_task(self, task_id: str) -> bool:
        if self._tasks.pop(task_id, None) is None:
            return False
        self._transcript_revisions = {
            revision_id: revision
            for revision_id, revision in self._transcript_revisions.items()
            if revision.task_id != task_id
        }
        return True

    def save_transcript_revision(self, revision: TranscriptRevision) -> None:
        if revision.revision_id in self._transcript_revisions or any(
            item.task_id == revision.task_id
            and item.revision_number == revision.revision_number
            for item in self._transcript_revisions.values()
        ):
            raise ValueError("该校对版本号已经存在，请刷新页面后基于最新版本继续校对。")
        self._transcript_revisions[revision.revision_id] = revision

    def list_transcript_revisions(self, task_id: str) -> list[TranscriptRevision]:
        return sorted(
            (
                item
                for item in self._transcript_revisions.values()
                if item.task_id == task_id
            ),
            key=lambda item: item.revision_number,
        )

    def get_transcript_revision(self, revision_id: str) -> TranscriptRevision | None:
        return self._transcript_revisions.get(revision_id)

    def save_sampling_checkpoint(self, checkpoint: SamplingCheckpoint) -> None:
        self._sampling_checkpoints[checkpoint.checkpoint_id] = checkpoint

    def list_sampling_checkpoints(
        self,
        keyword: str | None = None,
        tracking_batch_id: str | None = None,
    ) -> list[SamplingCheckpoint]:
        items: list[SamplingCheckpoint] = list(self._sampling_checkpoints.values())
        if tracking_batch_id:
            items = [
                item
                for item in items
                if item.tracking_batch_id == tracking_batch_id
            ]
        elif keyword:
            items = [
                item for item in items if item.keyword.casefold() == keyword.casefold()
            ]
        return sorted(items, key=lambda item: item.due_at)

    def save_search_batch(self, batch: SearchBatch) -> None:
        self._search_batches[batch.batch_id] = batch

    def get_search_batch(self, batch_id: str) -> SearchBatch | None:
        return self._search_batches.get(batch_id)

    def list_search_batches(self, limit: int = 20) -> list[SearchBatch]:
        return sorted(
            self._search_batches.values(),
            key=lambda item: item.created_at,
            reverse=True,
        )[:limit]

    def save_crawler_keyword_queue(self, queue: CrawlerKeywordQueue) -> None:
        self._crawler_keyword_queues[queue.queue_id] = queue

    def get_crawler_keyword_queue(self, queue_id: str) -> CrawlerKeywordQueue | None:
        return self._crawler_keyword_queues.get(queue_id)

    def list_crawler_keyword_queues(self, limit: int = 20) -> list[CrawlerKeywordQueue]:
        return sorted(
            self._crawler_keyword_queues.values(),
            key=lambda item: item.created_at,
            reverse=True,
        )[:limit]

    def get_publish_safety_state(
        self, platform: str, account_id: str
    ) -> PublishSafetyState | None:
        return self._publish_safety_states.get((platform, account_id))

    def update_publish_safety_state(self, state: PublishSafetyState) -> None:
        self._publish_safety_states[(state.platform, state.account_id)] = state

    def get_provider_safety_state(self, provider: str) -> ProviderSafetyState | None:
        return self._provider_safety_states.get(provider)

    def claim_provider_safety_lease(
        self,
        *,
        provider: str,
        run_id: str,
        now: datetime,
        lease_seconds: int,
        max_runs_in_window: int | None = None,
        rolling_window_seconds: int = 24 * 60 * 60,
    ) -> bool:
        state = self._provider_safety_states.get(provider)
        if state is not None:
            if state.blocked_until and state.blocked_until > now:
                return False
            if state.next_allowed_at and state.next_allowed_at > now:
                return False
            if (
                state.active_run_id
                and state.active_run_id != run_id
                and state.lease_expires_at
                and state.lease_expires_at > now
            ):
                return False
        window_start = state.rolling_window_started_at if state else None
        active_window = bool(
            window_start
            and now - window_start < timedelta(seconds=rolling_window_seconds)
        )
        current_runs = state.real_runs_in_window if state and active_window else 0
        if (
            max_runs_in_window is not None
            and max_runs_in_window > 0
            and current_runs >= max_runs_in_window
        ):
            return False
        self._provider_safety_states[provider] = ProviderSafetyState(
            provider=provider,
            active_run_id=run_id,
            lease_expires_at=now + timedelta(seconds=max(1, lease_seconds)),
            rolling_window_started_at=window_start if active_window else now,
            real_runs_in_window=current_runs + 1,
            updated_at=now,
        )
        return True

    def release_provider_safety_lease(
        self,
        *,
        provider: str,
        run_id: str,
        now: datetime,
        cooldown_seconds: int,
        safety_pause_seconds: int = 0,
        safety_reason: str | None = None,
    ) -> ProviderSafetyState:
        current = self._provider_safety_states.get(provider)
        existing_block = (
            current.blocked_until
            if current and current.blocked_until and current.blocked_until > now
            else None
        )
        requested_block = (
            now + timedelta(seconds=max(1, safety_pause_seconds))
            if safety_pause_seconds
            else None
        )
        blocked_until = max(
            (item for item in (existing_block, requested_block) if item is not None),
            default=None,
        )
        state = ProviderSafetyState(
            provider=provider,
            active_run_id=(
                current.active_run_id
                if current and current.active_run_id != run_id
                else None
            ),
            lease_expires_at=(
                current.lease_expires_at
                if current and current.active_run_id != run_id
                else None
            ),
            next_allowed_at=now + timedelta(seconds=max(0, cooldown_seconds)),
            blocked_until=blocked_until,
            blocked_reason=safety_reason
            if requested_block
            else (current.blocked_reason if current else None),
            rolling_window_started_at=(
                current.rolling_window_started_at if current else None
            ),
            real_runs_in_window=current.real_runs_in_window if current else 0,
            updated_at=now,
        )
        self._provider_safety_states[provider] = state
        return state

    def delete_search_batch(self, batch_id: str) -> bool:
        if self._search_batches.pop(batch_id, None) is None:
            return False
        run_ids = {
            run_id
            for run_id, run in self._platform_search_runs.items()
            if run.batch_id == batch_id
        }
        for run_id in run_ids:
            self._platform_search_runs.pop(run_id, None)
        for fingerprint, (run_id, _claimed_at, _status) in list(
            self._provider_request_guards.items()
        ):
            if run_id in run_ids:
                self._provider_request_guards.pop(fingerprint, None)
        return True

    def save_platform_search_run(self, run: PlatformSearchRun) -> None:
        self._platform_search_runs[run.run_id] = run

    def list_platform_search_runs(self, batch_id: str) -> list[PlatformSearchRun]:
        return sorted(
            (
                run
                for run in self._platform_search_runs.values()
                if run.batch_id == batch_id
            ),
            key=lambda item: (item.started_at, item.platform.value),
        )

    def find_cached_platform_search_run(
        self,
        *,
        provider: str,
        platform: Platform,
        keyword: str,
        published_window_days: int,
        hotspot_window_hours: int | None,
        requested_count: int,
        since: datetime,
        request_fingerprint: str | None = None,
    ) -> PlatformSearchRun | None:
        candidates: list[PlatformSearchRun] = []
        for run in self._platform_search_runs.values():
            batch = self._search_batches.get(run.batch_id)
            if (
                batch
                and run.provider == provider
                and run.platform == platform
                and run.status.value in {"succeeded", "partial"}
                and run.finished_at is not None
                and run.finished_at >= since
                and (
                    run.request_fingerprint == request_fingerprint
                    if request_fingerprint is not None
                    else (
                        batch.keyword.casefold() == keyword.casefold()
                        and batch.published_window_days == published_window_days
                        and batch.hotspot_window_hours == hotspot_window_hours
                        and batch.requested_count_per_platform == requested_count
                    )
                )
            ):
                candidates.append(run)
        return max(candidates, key=lambda item: item.finished_at or since, default=None)

    def monthly_platform_query_count(self, since: datetime) -> int:
        return sum(
            run.api_call_count
            for run in self._platform_search_runs.values()
            if run.started_at >= since
        )

    def monthly_platform_query_cost(self, since: datetime) -> float:
        search_cost = sum(
            run.billable_units or 0.0
            for run in self._platform_search_runs.values()
            if run.started_at >= since
        )
        media_cost = sum(
            attempt.billable_units or attempt.estimated_cost_cny or 0.0
            for attempt in self._media_resolution_attempts.values()
            if attempt.created_at >= since and attempt.api_call_count > 0
        )
        return search_cost + media_cost

    def try_claim_platform_search_request(
        self,
        *,
        fingerprint: str,
        run_id: str,
        claimed_at: datetime,
        ttl_seconds: int = 60,
        unit_price: float = 0.0,
        enforce_limits: bool = True,
        monthly_queries_limit: int | None = 100,
        monthly_cost_limit_cny: float | None = 10.0,
    ) -> str:
        previous = self._provider_request_guards.get(fingerprint)
        if previous:
            if previous[2] == "outcome_unknown":
                return "unresolved"
            if claimed_at - previous[1] < timedelta(seconds=ttl_seconds):
                return "duplicate"
        if enforce_limits:
            month_start = claimed_at.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            pending = [
                g
                for g in self._provider_request_guards.values()
                if g[2] == "claimed" and g[1] >= month_start
            ]
            used_count = self.monthly_platform_query_count(month_start) + len(pending)
            used_cost = self.monthly_platform_query_cost(month_start) + sum(
                float(g[3]) for g in pending
            )
            if (
                monthly_queries_limit is not None
                and used_count >= monthly_queries_limit
            ):
                return "count_limit"
            if (
                monthly_cost_limit_cny is not None
                and used_cost + unit_price > monthly_cost_limit_cny
            ):
                return "cost_limit"
        self._provider_request_guards[fingerprint] = (
            run_id,
            claimed_at,
            "claimed",
            str(unit_price),
        )
        return "ok"

    def mark_platform_search_request(
        self, fingerprint: str, status: str, updated_at: datetime
    ) -> None:
        previous = self._provider_request_guards.get(fingerprint)
        if previous:
            self._provider_request_guards[fingerprint] = (
                previous[0],
                updated_at,
                status,
            )

    def has_unresolved_platform_search_request(self, fingerprint: str) -> bool:
        guard = self._provider_request_guards.get(fingerprint)
        return bool(guard and guard[2] == "outcome_unknown")

    def resolve_platform_search_request(self, fingerprint: str) -> None:
        self._provider_request_guards.pop(fingerprint, None)

    def save_media_resolution_attempt(self, attempt: MediaResolutionAttempt) -> None:
        self._media_resolution_attempts[attempt.resolution_id] = attempt

    def get_media_resolution_attempt(
        self, resolution_id: str
    ) -> MediaResolutionAttempt | None:
        return self._media_resolution_attempts.get(resolution_id)

    def find_media_resolution_by_idempotency_key(
        self, idempotency_key: str
    ) -> MediaResolutionAttempt | None:
        for attempt in self._media_resolution_attempts.values():
            if attempt.idempotency_key == idempotency_key:
                return attempt
        return None

    def find_latest_media_resolution_for_candidate(
        self, candidate_id: str
    ) -> MediaResolutionAttempt | None:
        attempts = [
            attempt
            for attempt in self._media_resolution_attempts.values()
            if attempt.candidate_id == candidate_id
        ]
        return max(attempts, key=lambda item: item.updated_at, default=None)

    def claim_media_resolution_request(
        self,
        idempotency_key: str,
        resolution_id: str,
        claimed_at: datetime,
        ttl_seconds: int = 60,
    ) -> bool:
        previous = self._media_resolution_guards.get(idempotency_key)
        if previous:
            if previous[3] == "outcome_unknown":
                return False
            if claimed_at - previous[2] < timedelta(seconds=ttl_seconds):
                return False
        self._media_resolution_guards[idempotency_key] = (
            resolution_id,
            "",
            claimed_at,
            "claimed",
        )
        return True

    def mark_media_resolution_request(
        self, idempotency_key: str, status: str, updated_at: datetime
    ) -> None:
        previous = self._media_resolution_guards.get(idempotency_key)
        attempt = self.find_media_resolution_by_idempotency_key(idempotency_key)
        if previous:
            self._media_resolution_guards[idempotency_key] = (
                previous[0],
                attempt.candidate_id if attempt else previous[1],
                updated_at,
                status,
            )

    def has_unresolved_media_resolution(self, candidate_id: str) -> bool:
        return any(
            attempt.candidate_id == candidate_id
            and attempt.status == MediaResolutionStatus.OUTCOME_UNKNOWN
            for attempt in self._media_resolution_attempts.values()
        )

    # -- 流水线运行记录 --

    def save_pipeline_run(self, run: PipelineRun) -> None:
        self._pipeline_runs[run.run_id] = run

    def get_pipeline_run(self, run_id: str) -> PipelineRun | None:
        return self._pipeline_runs.get(run_id)

    def list_pipeline_runs(self, limit: int = 20) -> list[PipelineRun]:
        return sorted(
            self._pipeline_runs.values(),
            key=lambda item: item.created_at,
            reverse=True,
        )[:limit]

    def list_active_pipeline_runs(self) -> list[PipelineRun]:
        active = {"pending", "running", "paused"}
        return sorted(
            (run for run in self._pipeline_runs.values() if run.status.value in active),
            key=lambda item: item.created_at,
        )

    def claim_pipeline_run_transition(
        self,
        *,
        expected_run: PipelineRun,
        claimed_run: PipelineRun,
    ) -> bool:
        current = self._pipeline_runs.get(expected_run.run_id)
        if (
            current is None
            or current.updated_at != expected_run.updated_at
            or current.status != expected_run.status
            or current.current_stage != expected_run.current_stage
            or current.config.get("review_stage")
            != expected_run.config.get("review_stage")
        ):
            return False
        self._pipeline_runs[claimed_run.run_id] = claimed_run
        return True

    def delete_pipeline_run(self, run_id: str) -> bool:
        return self._pipeline_runs.pop(run_id, None) is not None

    def delete_all_pipeline_runs(self) -> int:
        deleted_count = len(self._pipeline_runs)
        self._pipeline_runs.clear()
        return deleted_count

    def save_production_batch(self, batch: ProductionBatch) -> None:
        self._production_batches[batch.batch_id] = batch

    def get_production_batch(self, batch_id: str) -> ProductionBatch | None:
        return self._production_batches.get(batch_id)

    def list_production_batches(self, limit: int = 100) -> list[ProductionBatch]:
        return sorted(
            self._production_batches.values(),
            key=lambda item: item.created_at,
            reverse=True,
        )[:limit]

    def claim_production_operation(
        self,
        *,
        operation_type: str,
        idempotency_key: str,
        request_hash: str,
        resource_id: str,
        created_at: str,
    ) -> bool:
        key = (operation_type, idempotency_key)
        if key in self._production_operations:
            return False
        if any(
            record["resource_id"] == resource_id and record["state"] == "pending"
            for record in self._production_operations.values()
        ):
            return False
        self._production_operations[key] = {
            "operation_type": operation_type,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "resource_id": resource_id,
            "state": "pending",
            "error_message": None,
            "created_at": created_at,
            "updated_at": created_at,
        }
        return True

    def get_production_operation(
        self,
        *,
        operation_type: str,
        idempotency_key: str,
    ) -> dict | None:
        record = self._production_operations.get((operation_type, idempotency_key))
        return dict(record) if record is not None else None

    def reclaim_production_operation(
        self,
        *,
        operation_type: str,
        idempotency_key: str,
        expected_updated_at: str,
        updated_at: str,
    ) -> bool:
        record = self._production_operations.get((operation_type, idempotency_key))
        if (
            record is None
            or record["state"] != "pending"
            or record["updated_at"] != expected_updated_at
        ):
            return False
        record["updated_at"] = updated_at
        return True

    def complete_production_operation(
        self,
        *,
        operation_type: str,
        idempotency_key: str,
        request_hash: str,
        state: str,
        updated_at: str,
        resource_id: str,
        batch: ProductionBatch | None = None,
        runs: list[PipelineRun] | None = None,
        error_message: str | None = None,
    ) -> None:
        key = (operation_type, idempotency_key)
        operation = self._production_operations[key]
        if operation["request_hash"] != request_hash or operation["state"] != "pending":
            raise ValueError("生产操作幂等记录与请求哈希不一致。")
        for run in runs or []:
            self._pipeline_runs[run.run_id] = run
        if batch is not None:
            self._production_batches[batch.batch_id] = batch
        operation.update(
            {
                "state": state,
                "resource_id": resource_id,
                "error_message": error_message,
                "updated_at": updated_at,
            }
        )

    def save_video_editor_batch(self, batch) -> None:
        self._video_editor_batches[batch.batch_id] = batch

    def get_video_editor_batch(self, batch_id: str):
        return self._video_editor_batches.get(batch_id)

    def list_video_editor_batches(self, limit: int = 100):
        return sorted(
            self._video_editor_batches.values(),
            key=lambda item: item.created_at,
            reverse=True,
        )[:limit]

    def save_video_editor_quote(
        self,
        *,
        quote_id: str,
        source_id: str,
        output_profile: str,
        target_platform: str,
        expires_at: str,
        created_at: str,
        payload: dict,
    ) -> None:
        self._video_editor_quotes[quote_id] = {
            "quote_id": quote_id,
            "source_id": source_id,
            "output_profile": output_profile,
            "target_platform": target_platform,
            "expires_at": expires_at,
            "created_at": created_at,
            "payload": payload,
        }

    def get_video_editor_quote(self, quote_id: str):
        return self._video_editor_quotes.get(quote_id)

    def claim_video_editor_operation(
        self,
        *,
        idempotency_key: str,
        operation_type: str,
        request_hash: str,
        created_at: str,
    ) -> bool:
        if idempotency_key in self._video_editor_operations:
            return False
        self._video_editor_operations[idempotency_key] = {
            "idempotency_key": idempotency_key,
            "operation_type": operation_type,
            "request_hash": request_hash,
            "state": "pending",
            "resource_id": None,
            "response": None,
            "error_message": None,
            "created_at": created_at,
            "updated_at": created_at,
        }
        return True

    def get_video_editor_operation(self, idempotency_key: str):
        return self._video_editor_operations.get(idempotency_key)

    def delete_video_editor_operation(self, idempotency_key: str) -> bool:
        return self._video_editor_operations.pop(idempotency_key, None) is not None

    def complete_video_editor_operation(
        self,
        *,
        idempotency_key: str,
        state: str,
        updated_at: str,
        resource_id: str | None = None,
        response: dict | None = None,
        error_message: str | None = None,
    ) -> None:
        operation = self._video_editor_operations[idempotency_key]
        operation.update(
            {
                "state": state,
                "resource_id": resource_id,
                "response": response,
                "error_message": error_message,
                "updated_at": updated_at,
            }
        )

    def save_video_editor_cloud_job(self, **job) -> None:
        self._video_editor_cloud_jobs[job["job_key"]] = dict(job)

    def list_video_editor_cloud_jobs(self, batch_id: str) -> list[dict]:
        return [
            item
            for item in self._video_editor_cloud_jobs.values()
            if item["batch_id"] == batch_id
        ]
