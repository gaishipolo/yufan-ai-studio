"""生图模块的周期任务：把卡在 `queued` / `running` 的记录捞回来。

## 为什么要这个任务

队列是进程内的（`queue_driver=memory`）。进程重启时，队列里还没执行的提交动作
**全部消失**，而记录仍然是 `queued`：页面上永远转圈，用户以为系统坏了，
日志里一句错都没有——这是最难解释的一类故障。

框架没有持久队列，也不该为了这个立刻上 Redis，所以用「定期巡检 + 重投」兜底：
数据库是唯一的事实来源，队列只是加速器。

## 两类「卡住」，处置完全相反（R13-04）

| 形状 | 处置 | 理由 |
|---|---|---|
| 没有 `external_id` | `dispatch()` | 提交动作没落库，重投等于把那次提交再做一遍，安全 |
| 有 `external_id`，快照 `async` | `resume()` | 已受理，只能续轮询；重投就是第二次计费 |
| 有 `external_id`，`sync` / 无快照 | 不动，交人工 | 续查不了也判不死，凭任务号核对 |

以前用一条 `external_id is None` 把两类混在一起：真正需要接管的「轮询链断掉」
记录必然有任务号，恰好被这个条件全挡在外面，被捞到的只是本来就会重新提交的
那一类（R12-06 只修了一半，R13-04 补上）。启动恢复器能 `resume()` 这些记录，
但它每次进程启动只跑一次、上限 100 条——轮询链在两次重启之间断掉就再没人管。

## 为什么放在模块里而不是框架里

「多久算卡住」「卡住之后是重投还是直接置失败」是**业务判断**，不同能力的答案不同
（生图 6 秒一轮，视频要 3 分钟）。框架只提供「定期发生」这个插槽（手册 §5.3），
具体多久问一次由模块自己说。

## 幂等（以及它的边界）

`dispatch` 用幂等键 `submit:image:<id>`，重投不会变成第二次提交；
但这只在 job **还没被 dequeue** 时成立——`MemoryJobQueue` 出队就会移除幂等键，
所以「提交动作正在执行」期间重投会真的排上第二个 worker。因此巡检必须同时满足：

1. 阈值大于供应商的单次提交耗时（由 `timeout_sec` 推导，不写死 90 秒）；
2. 按「提交开始时间」而不是「创建时间」判陈旧，让排队很久但刚开始提交的任务不算孤儿。

少了这两条，一次合法跑到 100 秒的 Agnes 调用会被巡检判死，随后第一个 worker
拿到结果又把状态覆盖回 running/succeeded（R11-02 现象 C）。
`resume()` 自带幂等（记录终态早退），但**绝不能**用在「有 external_id」以外的
记录上——它对没有任务号的记录无从续起。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from core.config import settings
from core.logging import get_logger
from core.scheduler import periodic
from db.session import get_session_factory
from generation.runner import get_runner
from generation.spec import STATUS_QUEUED, STATUS_RUNNING
from infra.ai.base import Capability
from infra.driver_config import registered_submit_budgets
from modules.image.models import ImageJob
from modules.image.pipeline import KIND_IMAGE

logger = get_logger(__name__)

# 兜底下限：供应商没声明 timeout_sec 时至少要留这么多
_MIN_STALE_AFTER_SEC = 90.0
# 余量：一次提交可能占满整个超时，再留半个超时给队列排队与落库
_TIMEOUT_MARGIN = 1.5
# 一次最多补投这么多，防止供应商故障后一轮把队列灌爆
BATCH = 50


def _stale_after_sec() -> float | None:
    """巡检阈值 = 声明了 IMAGE 提交预算的驱动里最长 `timeout_sec` × 1.5（至少 90 秒）。

    不是「遍历全局注册表按字段名 `timeout_sec` 猜语义」：成图下载、视频供应商、
    甚至一个与生图毫无关系的驱动都能把阈值拖到 90 分钟，或在配置缺项时被
    `except: continue` 悄悄压回 90 秒——正好退回误伤在途任务的窗口（R12-03）。
    现在由驱动**自己声明** `submit_budget_for`，配置坏了直接抛，不静默跳过。

    一条声明都没有时返回 None：宁可本轮不巡检并打 error，也不能用一个
    猜出来的阈值去判死正在跑的任务。
    """
    from infra.ai import agnes  # noqa: F401  确保 IMAGE 供应商的声明已登记

    budgets = registered_submit_budgets(Capability.IMAGE.value)
    if not budgets:
        logger.error(
            "没有任何驱动声明 IMAGE 提交预算（submit_budget_for），本轮巡检跳过："
            "请给 IMAGE 供应商的 DriverSettings 补上声明"
        )
        return None
    return max(_MIN_STALE_AFTER_SEC, max(budgets) * _TIMEOUT_MARGIN)


def _aware(value: datetime) -> datetime:
    """sqlite 读回来的时间没有时区，按 UTC 补齐（与 `generation.runner` 同一处理）。"""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _clock(row: ImageJob) -> datetime:
    """这条任务「开始干活」的时刻：提交开始优先，没提交过就用创建时间。"""
    return _aware(row.started_at or row.created_at)


@periodic("image.reconcile", every=300, immediate=True)
async def reconcile_stale_jobs() -> None:
    stale_after = _stale_after_sec()
    if stale_after is None:
        return
    cutoff = datetime.now(tz=UTC) - timedelta(seconds=stale_after)
    # sqlite 的列存的是 UTC naive 字符串，SQL 条件必须用同样口径；
    # PG 的 DateTime(timezone=True) 反而要 aware cutoff。按 dialect 分支，
    # 别把这段跨库债留在注释里等换库时踩（R12-08）。
    cutoff_expr = cutoff.replace(tzinfo=None) if settings.is_sqlite else cutoff
    started = func.coalesce(ImageJob.started_at, ImageJob.created_at)

    async with get_session_factory()() as session:
        # 两类形状分开查，不靠一个 `external_id is None` 一刀切（R13-04）：
        # 前者「提交没落库」，后者「供应商已受理但轮询链断了」。
        unsubmitted = (
            await session.scalars(
                select(ImageJob)
                .where(
                    ImageJob.status.in_((STATUS_QUEUED, STATUS_RUNNING)),
                    ImageJob.external_id.is_(None),
                    started < cutoff_expr,
                )
                .order_by(started, ImageJob.id)
                .limit(BATCH)
            )
        ).all()
        accepted = (
            await session.scalars(
                select(ImageJob)
                .where(
                    ImageJob.status == STATUS_RUNNING,
                    ImageJob.external_id.is_not(None),
                    started < cutoff_expr,
                )
                .order_by(started, ImageJob.id)
                .limit(BATCH)
            )
        ).all()

    # 二次确认只用于兜时区异常，不再是「切 BATCH」的手段：BATCH 已在 SQL 里。
    stale_unsubmitted = [row for row in unsubmitted if _clock(row) < cutoff]
    # 有任务号的记录里，只有异步供应商能续轮询；同步供应商连查都没地方查，
    # 贸然重投就是第二次计费。这一类保持原状，等人凭任务号核对。
    resume_poll = [
        row for row in accepted if _clock(row) < cutoff and row.submit_mode == "async"
    ]
    needs_human = [
        row for row in accepted if _clock(row) < cutoff and row.submit_mode != "async"
    ]

    await _redispatch_unsubmitted(stale_unsubmitted, stale_after)
    await _resume_accepted(resume_poll, stale_after)
    if needs_human:
        logger.warning(
            "发现 %d 条生图任务停在 running 且已有供应商任务号（>%d 秒），"
            "但快照显示无法续轮询（sync 或无快照），不自动重投，请凭任务号核对：%s",
            len(needs_human),
            int(stale_after),
            ", ".join(f"{row.id}={row.external_id}" for row in needs_human),
        )


async def _redispatch_unsubmitted(rows: list[ImageJob], stale_after: float) -> None:
    """提交动作还没落库：重投是安全的（幂等键还在时连第二次提交都不会发生）。"""
    if not rows:
        return
    redispatch = [row for row in rows if not row.submit_attempts]
    needs_review = [row for row in rows if row.submit_attempts]
    if redispatch:
        logger.warning(
            "发现 %d 条生图任务停在 queued/running 且未拿到供应商任务号（>%d 秒），重新投递：%s",
            len(redispatch),
            int(stale_after),
            ", ".join(row.id for row in redispatch),
        )
    if needs_review:
        # 这类记录会被 handle_submit 的防重复计费分支**置为 failed** 等人核对，
        # 不是真的「重投」。日志必须与结果一致，否则运维会按重投去理解一条死记录。
        logger.warning(
            "发现 %d 条生图任务提交结果未知（>%d 秒），交由框架置为待人工核对：%s",
            len(needs_review),
            int(stale_after),
            ", ".join(row.id for row in needs_review),
        )
    for row in rows:
        await get_runner().dispatch(KIND_IMAGE, row.id)


async def _resume_accepted(rows: list[ImageJob], stale_after: float) -> None:
    """有任务号且能续轮询：只 `resume()`，绝不 `dispatch()`。

    这是 R12-01 的计费红线：记录一旦有 `external_id`，供应商侧任务就在跑，
    重新 `dispatch` 会走进 `handle_submit` 再提交一次。
    """
    if not rows:
        return
    logger.warning(
        "发现 %d 条生图任务停在 running 且轮询链可能已断（>%d 秒），续接轮询：%s",
        len(rows),
        int(stale_after),
        ", ".join(row.id for row in rows),
    )
    runner = get_runner()
    for row in rows:
        try:
            await runner.resume(KIND_IMAGE, row.id)
        except Exception:  # noqa: BLE001 - 单条失败不阻塞其余记录
            logger.exception("续接轮询失败 id=%s", row.id)
