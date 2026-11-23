"""用量看板的聚合服务。

它**不认识**任何一张业务表：视频、生图、将来的音频，各自实现一份
`core.ports.usage_metrics` 的读取器并注册进来，这里只负责把各来源的结果合并。

## 合并的三条规则

1. **数相加，标签取可读的那个**。同一个模型可能被视频和生图两边都用，
   按 `(维度, 键)` 合并成一行；标签优先保留「可读名」而不是退化成 ID。
2. **趋势补零**。来源方只报「有数据的桶」，空的那天由这里补一个 0 ——
   否则折线会跳过没做生成的那天，看起来像「那天没生成过」而不是「那天是 0」。
3. **越权降级而不是 403**。详见 `_resolve_scope` 的注释。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from core.context import CurrentUser
from core.ports.usage_metrics import (
    UsageBreakdownItem,
    UsageQuery,
    UsageRecord,
    as_utc,
    usage_metrics_providers,
)
from core.response import Page, PageParams
from modules.usage.permissions import READ_ALL
from modules.usage.schema import (
    UsageBreakdownItemOut,
    UsageGranularity,
    UsageOverviewOut,
    UsageRecordOut,
    UsageScope,
    UsageSourceOut,
    UsageSummaryOut,
    UsageTrendPointOut,
)

#: 默认区间（天）。看板默认看最近一个月 —— 再短看不出趋势，再长没人等。
DEFAULT_RANGE_DAYS = 30

#: 单次查询允许的最大区间（天）。看板是给人看的统计，不是数据仓库；
#: 一年以上的区间几乎没人看，却会把整张表扫一遍。
MAX_RANGE_DAYS = 366


class UsageService:
    """用量看板的聚合层。"""

    def __init__(self, actor: CurrentUser) -> None:
        self._actor = actor

    # ------------------------------------------------------------------ #
    # 区间与范围
    # ------------------------------------------------------------------ #
    def _resolve_scope(self, scope: UsageScope) -> UsageScope:
        """越权请求降级为「只看自己」，而不是 403。

        为什么不是 403：前端只在持有 `usage:read:all` 时才显示「全部成员」这个
        选项，正常操作路径不会越权。但如果有人直接改了 URL 参数，403 会让整页
        报错 —— 而他本来想看的那部分数据（自己的）**是能看**的。降级之后把实际
        生效的 scope 如实写回响应，页面据此提示「当前只看自己」。
        """
        if scope == UsageScope.ALL and not self._actor.has(READ_ALL):
            return UsageScope.MINE
        return scope

    def _resolve_range(self, start: date | None, end: date | None) -> tuple[datetime, datetime]:
        """把「日期」口径的区间收敛成 UTC 的闭区间。

        `end` 是**含**的：传 `2026-11-22` 表示含 11-22 当天。
        """
        today = datetime.now(UTC).date()
        resolved_end = end or today
        resolved_start = start or (resolved_end - timedelta(days=DEFAULT_RANGE_DAYS - 1))
        if resolved_start > resolved_end:
            # 区间写反了：当作反过来读，比报错更省事（两个日期都是用户自己填的）
            resolved_start, resolved_end = resolved_end, resolved_start
        earliest = resolved_end - timedelta(days=MAX_RANGE_DAYS - 1)
        if resolved_start < earliest:
            resolved_start = earliest
        return (
            datetime.combine(resolved_start, time.min, tzinfo=UTC),
            datetime.combine(resolved_end, time.max, tzinfo=UTC),
        )

    def _query(
        self,
        scope: UsageScope,
        start_at: datetime,
        end_at: datetime,
        granularity: UsageGranularity,
        *,
        limit: int = 0,
        offset: int = 0,
    ) -> UsageQuery:
        return UsageQuery(
            actor_id=self._actor.id,
            can_read_all=scope == UsageScope.ALL,
            scope=scope.value,
            start=start_at,
            end=end_at,
            granularity=granularity.value,
            limit=limit,
            offset=offset,
        )

    # ------------------------------------------------------------------ #
    # 总览
    # ------------------------------------------------------------------ #
    async def overview(
        self,
        *,
        start: date | None = None,
        end: date | None = None,
        granularity: UsageGranularity = UsageGranularity.DAY,
        scope: UsageScope = UsageScope.MINE,
    ) -> UsageOverviewOut:
        """一次拿齐汇总 / 趋势 / 构成。

        合成一个接口而不是拆成 summary / trend / breakdown 三个：这三个问题
        问的是同一个区间、同一个范围，拆开就要跑三遍聚合，还可能出现
        「三份数据来自三个不同时刻」的自相矛盾。
        """
        effective_scope = self._resolve_scope(scope)
        start_at, end_at = self._resolve_range(start, end)
        query = self._query(effective_scope, start_at, end_at, granularity)

        aggregates = []
        for _key, provider in usage_metrics_providers():
            aggregates.append(await provider.aggregate(query))

        summary = self._merge_summary(aggregates, start_at, end_at, granularity, effective_scope)
        trend = self._merge_trend(aggregates, start_at, end_at, granularity)
        breakdown = self._merge_breakdown(aggregates, summary.total)
        return UsageOverviewOut(
            summary=summary,
            trend=trend,
            breakdown=breakdown,
            sources=[agg.source for agg in aggregates],
        )

    def _merge_summary(
        self,
        aggregates: list,
        start_at: datetime,
        end_at: datetime,
        granularity: UsageGranularity,
        scope: UsageScope,
    ) -> UsageSummaryOut:
        total = succeeded = failed = pending = quantity = duration_sec = 0
        sources: list[UsageSourceOut] = []
        for agg in aggregates:
            item = agg.summary
            total += item.total
            succeeded += item.succeeded
            failed += item.failed
            pending += item.pending
            quantity += item.quantity
            duration_sec += item.duration_sec
            sources.append(
                UsageSourceOut(
                    source=agg.source,
                    total=item.total,
                    succeeded=item.succeeded,
                    failed=item.failed,
                    pending=item.pending,
                    quantity=item.quantity,
                    duration_sec=item.duration_sec,
                )
            )
        return UsageSummaryOut(
            start=start_at,
            end=end_at,
            granularity=granularity,
            scope=scope,
            total=total,
            succeeded=succeeded,
            failed=failed,
            pending=pending,
            success_rate=(succeeded / total) if total else 0.0,
            quantity=quantity,
            duration_sec=duration_sec,
            avg_duration_sec=(duration_sec / succeeded) if succeeded else 0.0,
            sources=sources,
        )

    def _merge_trend(
        self,
        aggregates: list,
        start_at: datetime,
        end_at: datetime,
        granularity: UsageGranularity,
    ) -> list[UsageTrendPointOut]:
        merged: dict[str, UsageTrendPointOut] = {}
        for agg in aggregates:
            for point in agg.trend:
                current = merged.get(point.bucket)
                if current is None:
                    merged[point.bucket] = UsageTrendPointOut(
                        bucket=point.bucket,
                        total=point.total,
                        succeeded=point.succeeded,
                        failed=point.failed,
                        pending=point.pending,
                        quantity=point.quantity,
                        duration_sec=point.duration_sec,
                    )
                    continue
                current.total += point.total
                current.succeeded += point.succeeded
                current.failed += point.failed
                current.pending += point.pending
                current.quantity += point.quantity
                current.duration_sec += point.duration_sec
        # 补零：来源方只报有数据的桶，空的天由这里补，否则折线会「跳过」那天
        for bucket in _bucket_keys(start_at, end_at, granularity):
            merged.setdefault(bucket, _zero_point(bucket))
        return [merged[key] for key in sorted(merged)]

    def _merge_breakdown(self, aggregates: list, total: int) -> list[UsageBreakdownItemOut]:
        merged: dict[tuple[str, str], UsageBreakdownItem] = {}
        for agg in aggregates:
            for item in agg.breakdown:
                key = (item.dimension, item.key)
                current = merged.get(key)
                if current is None:
                    merged[key] = UsageBreakdownItem(
                        dimension=item.dimension,
                        key=item.key,
                        label=item.label,
                        source=item.source,
                        total=item.total,
                        quantity=item.quantity,
                        duration_sec=item.duration_sec,
                    )
                    continue
                current.total += item.total
                current.quantity += item.quantity
                current.duration_sec += item.duration_sec
                current.label = _pick_label(current.label, current.key, item.label)
        result = [
            UsageBreakdownItemOut(
                dimension=item.dimension,
                key=item.key,
                label=item.label,
                total=item.total,
                quantity=item.quantity,
                duration_sec=item.duration_sec,
                share=(item.total / total) if total else 0.0,
            )
            for item in merged.values()
        ]
        result.sort(key=lambda row: (row.dimension, -row.total, row.label))
        return result

    # ------------------------------------------------------------------ #
    # 明细
    # ------------------------------------------------------------------ #
    async def records(
        self,
        *,
        start: date | None = None,
        end: date | None = None,
        scope: UsageScope = UsageScope.MINE,
        params: PageParams | None = None,
    ) -> Page[UsageRecordOut]:
        """用量明细（跨来源合并后分页）。"""
        page_params = params or PageParams()
        effective_scope = self._resolve_scope(scope)
        start_at, end_at = self._resolve_range(start, end)
        query = self._query(
            effective_scope,
            start_at,
            end_at,
            UsageGranularity.DAY,
            limit=page_params.limit,
            offset=page_params.offset,
        )

        collected: list[UsageRecord] = []
        total = 0
        for _key, provider in usage_metrics_providers():
            page = await provider.records(query)
            total += page.total
            collected.extend(page.items)

        # 各来源各自排序，合并后必须再排一次，否则跨来源的页码会乱
        # （视频按时间排完，生图排在后面 —— 但全局顺序应该是按时间的）。
        collected.sort(key=lambda record: (as_utc(record.created_at), record.id), reverse=True)
        window = collected[page_params.offset : page_params.offset + page_params.limit]
        items = [
            UsageRecordOut(
                id=record.id,
                source=record.source,
                title=record.title,
                status=record.status,
                model_id=record.model_id,
                model_name=record.model_name,
                quantity=record.quantity,
                duration_sec=record.duration_sec,
                created_at=record.created_at,
                owner_id=record.owner_id,
                owner_name=record.owner_name,
                detail_url=record.detail_url,
            )
            for record in window
        ]
        return Page.create(items, total, page_params)


def _zero_point(bucket: str) -> UsageTrendPointOut:
    """一个没有数据的桶。

    出参契约里所有字段都是必填（没有默认值），所以补零必须**显式**给全
    0 —— 少给一个字段就是构造失败，而不是「缺一个数」。
    """
    return UsageTrendPointOut(
        bucket=bucket,
        total=0,
        succeeded=0,
        failed=0,
        pending=0,
        quantity=0,
        duration_sec=0,
    )


def _bucket_keys(
    start_at: datetime,
    end_at: datetime,
    granularity: UsageGranularity,
) -> list[str]:
    """区间内应有的全部时间桶（用来补零）。"""
    keys: list[str] = []
    cursor = start_at
    step = timedelta(days=7 if granularity == UsageGranularity.WEEK else 1)
    if granularity == UsageGranularity.WEEK:
        cursor = cursor - timedelta(days=cursor.weekday())
    while cursor <= end_at:
        keys.append(cursor.strftime("%Y-%m-%d"))
        cursor += step
    return keys


def _pick_label(current: str, key: str, candidate: str) -> str:
    """合并标签时优先保留「可读名」，不要退化成 ID。

    生图记录没有使用人可读名（`image_jobs` 只存了 `owner_id`），视频记录有。
    两个来源合并时如果先到的是生图那侧，标签就会是 ID —— 这里把有名字的那侧补上。
    """
    if current and current != key:
        return current
    if candidate and candidate != key:
        return candidate
    return current or candidate


__all__ = ["DEFAULT_RANGE_DAYS", "MAX_RANGE_DAYS", "UsageService"]
