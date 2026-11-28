"""生成任务的启动恢复：把「按 kind 捞未终态记录」上移到框架。

## 为什么需要它

`generation.runner` 早就提供了 `resume()`（只续轮询、绝不重新提交），
`ModuleSpec.startup` 也早就存在——但两者都是**可选插槽**。R11-01 的真实事故是：
新加的 `image` 模块接齐了"让它跑起来"的插槽（pipeline / dispatch / reconcile），
"出事后能恢复"的插槽一个没接，而 292 个用例、ruff、mypy、契约检查全部照常通过。
进程在提交成功后落结果前被 kill，那条 `running` 记录就永远挂在页面上。

所以恢复不能靠"新模块作者记得接"：框架提供通用恢复器，模块只需在
`ModuleSpec.recovery_kinds` 里声明自己负责哪些 kind。已有自定义恢复器的模块
（task / template）保留 `startup`，框架识别到就跳过，不重复处置。

## 三类未终态记录

| 形状 | 处置 | 理由 |
|---|---|---|
| 有 `external_id`，供应商支持轮询 | `runner.resume()` | 只续轮询，绝不重新提交、绝不二次计费 |
| 有 `external_id`，同步供应商 | 置失败等人重试 | `poll()` 不支持轮询会判死，结果已无法找回 |
| 无 `external_id`，`submit_attempts>0` | 置失败 + outbox | 防重复计费：宁可等人核对，不自动重提 |
| 无 `external_id`，`attempts=0` | `runner.dispatch()` | 建了记录却没进队列（进程内队列重启即丢） |

## 模块要提供什么

一个可选的 `pipeline.list_unfinished(session, limit, stale_after_sec=None)`，
返回未终态的 `GenerationRecord` 列表。启动恢复不传阈值（扫全部），周期巡检
传阈值（只扫久未写入的）。框架不认识任何 ORM 实体，差异仍由各模块吸收。

## 谁来决定「这次启动要不要恢复」

`run_startup_recovery()` 是**唯一入口**：模块自带的 `startup` 与框架恢复
都在它里面执行。`worker_enabled` 表达的是「本实例不消费队列、因此不该做恢复」，
这是**动作级**约束，就必须贴在「执行恢复」这个动作上，而不是贴在「恢复由谁
实现」上——R12-09 只给框架恢复器加了门控，模块自己的 `startup` 绕过了它，
于是只读 API 实例每次启动都在改写记录状态、往无人消费的队列里投任务（R13-01）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from core.logging import get_logger
from core.ports.submission_ledger import lookup_recovery_external_id
from db.outbox import dispatch_pending
from db.outbox import record as record_event
from db.session import get_session_factory
from generation.events import EVT_GENERATION_FAILED
from generation.pipeline import get_pipeline
from generation.runner import SUBMIT_UNKNOWN_MESSAGE, GenerationRunner, get_runner
from generation.spec import GenerationRecord
from generation.store import ExternalIdRecoverable, ResettableForResubmit
from infra import get_model_router
from infra.ai.base import PollCapable

logger = get_logger(__name__)

RECOVERY_BATCH = 100

SYNC_PROVIDER_ORPHAN_MESSAGE = (
    "生成结果未能落库：该供应商是同步接口，不支持续查；本次提交可能已产生费用，"
    "请凭供应商任务号（externalId）核对结果后再决定是否重试"
)


@runtime_checkable
class RecoverablePipeline(Protocol):
    async def list_unfinished(
        self,
        session: AsyncSession,
        limit: int | None = None,
        *,
        stale_after_sec: int | None = None,
    ) -> Sequence[GenerationRecord]: ...


def _supports_poll(record: GenerationRecord) -> bool:
    """同步供应商（Agnes）不能续轮询。

    优先读**记录自身的形态快照**：供应商下线、换配置、或测试环境只加载了 echo
    时，按当前模型目录重新解析会把一条历史 Agnes 记录认成别的 provider，
    分类随之翻转（R12-05）。老记录没有快照才回退到目录，且必须严格解析：
    `provider_for()` 有「只有一个供应商时兜底」的行为，直接用会把不存在的
    模型也解析成某个 provider，绝不能拿它给历史记录分类。

    两个不确定都选安全的一边：查不到 / 没声明 `supports_poll` / 解析失败
    一律返回 False —— 置失败等人核对，而不是 `resume()` 到一个恒返回
    FAILED 的 `poll()` 上把记录判死（R11-01）。
    """
    if record.submit_mode is not None:
        return record.submit_mode == "async"

    try:
        router = get_model_router()
        if router.resolve(record.model) is None:
            logger.warning(
                "恢复分类：模型目录里已没有 %r 的声明，按不支持续查处理 id=%s",
                record.model,
                record.id,
            )
            return False
        provider = router.provider_for(record.model)
    except Exception:  # noqa: BLE001 - 模型目录被改过，不该让启动恢复整个崩掉
        logger.exception("恢复分类：解析供应商失败，按不支持续查处理 id=%s", record.id)
        return False
    # 协议判定：没实现 `PollCapable` 的供应商一律当「不能续查」（安全侧）。
    return isinstance(provider, PollCapable) and bool(provider.supports_poll)


async def _fail_record(kind: str, record: GenerationRecord, message: str) -> bool:
    """把一条恢复期记录置失败，并把失败事件写进同一个 outbox 事务。

    返回**是否真的写了终态**（`mark_failed` 的条件更新没命中就是 False）。
    事件必须和业务行同事务：commit 之后再 publish 的窗口里崩掉，会出现
    「记录失败、事件永久消失」（R4-10）。
    """
    async with get_session_factory()() as session:
        store = get_pipeline(kind).store(session)
        # `mark_failed` 自己带终态谓词；不先 `load()` 是为了绕开业务加载器里的
        # 参考素材校验：素材坏了也不能阻止把这条记录判死/发事件。
        if not await store.mark_failed(record.id, message):
            logger.info("记录已终态，跳过失败收尾 kind=%s id=%s", kind, record.id)
            return False
        await record_event(
            session,
            EVT_GENERATION_FAILED,
            kind=kind,
            record_id=record.id,
            owner_id=record.owner_id,
            error=message,
        )
        await session.commit()
    return True


async def _mark_running(kind: str, record: GenerationRecord) -> bool:
    """续轮询前把状态置为 running；记录已终态/不存在时返回 False。"""
    async with get_session_factory()() as session:
        store = get_pipeline(kind).store(session)
        if not await store.mark_progress(record.id, record.progress):
            return False
        await session.commit()
    return True


async def _reset_for_resubmit(kind: str, record: GenerationRecord) -> bool:
    """派发前复位展示现场；存储不支持这项能力时直接放行。

    返回 False = 记录已终态/不存在，调用方不该再把它塞进队列。
    """
    async with get_session_factory()() as session:
        store = get_pipeline(kind).store(session)
        if not isinstance(store, ResettableForResubmit):
            return True
        if not await store.reset_for_resubmit(record.id):
            return False
        await session.commit()
    return True


async def _recover_external_id(kind: str, record: GenerationRecord) -> GenerationRecord | None:
    """从提交台账把凭证捡回来，回填业务表。

    ## 为什么需要它

    驱动的 `submit` 拿到上游标识的**同一个瞬间**就落台账（台账实现在
    `modules/model_driver/kit/submission_guard.py`），而业务表的
    `record.external_id` 是 submit 返回**之后**才写。这中间崩了的话：

    - 业务表说「没提交过」（→ 判死等人核对 → 上游已经在跑的任务白丢）；
    - 台账却记着「提交过、凭证在这儿」。

    回填把凭证捡回来，记录就能照常**续接轮询**。

    ## 只回填「最后一次提交尝试确实成功」的行

    台账是追加式历史：同一记录可能有多轮（4xx 后框架自动重试、人工重提）。
    判定在 kit 里做（`find_recoverable_external_id`）：只看**最后一轮**，且它必须
    是「已提交」。最后一轮若是被拒 / 提交结果未知 → 不回退到更早的成功轮次
    （那是上一轮的结果；人工重提的语义正是「要新的」）。

    ## 回填不动 `submit_attempts`

    那是「这次提交的结果还没落库」的证据；复位了，后续任何失败都会被当成
    「没提交过」而重新提交（重复计费风险）。

    返回**回填后的记录快照**，或 `None`（没有凭证 / 这个 kind 没接回填能力）。
    """
    candidate = await lookup_recovery_external_id(record.id)
    if not candidate:
        return None
    async with get_session_factory()() as session:
        store = get_pipeline(kind).store(session)
        if not isinstance(store, ExternalIdRecoverable):
            # 这个 kind 没接回填能力（如 model_driver 的作业）→ 保持现状。
            return None
        if not await store.set_external_id(record.id, candidate):
            # 记录已终态 / 不存在 → 有人处理过了，不回填。
            return None
        await session.commit()
        refreshed = await store.load(record.id)
    if refreshed is None:
        return None
    logger.info(
        "从提交台账回填凭证：kind=%s id=%s external_id=%s",
        kind,
        record.id,
        candidate,
    )
    return refreshed


async def recover_kind(
    kind: str,
    *,
    runner: GenerationRunner | None = None,
    limit: int | None = RECOVERY_BATCH,
    stale_after_sec: int | None = None,
) -> dict[str, int]:
    """恢复某一类生成能力的未终态记录，返回各类处置的条数。

    `stale_after_sec` 只给周期巡检用：启动恢复传 None，扫全部未终态记录；
    传阈值时只扫「超过 N 秒没有任何写入」的记录。`limit=None` 表示不截断
    （启动恢复），周期巡检传批次上限，避免一轮把所有残留排进队列。
    """
    pipeline = get_pipeline(kind)
    if not isinstance(pipeline, RecoverablePipeline):
        raise RuntimeError(
            f"kind={kind!r} 声明了框架恢复但流水线没有 list_unfinished("
            "session, limit, stale_after_sec)；要么实现它，要么改用模块自己的 startup"
        )

    active = runner or get_runner()
    async with get_session_factory()() as session:
        records = list(
            await pipeline.list_unfinished(
                session, limit, stale_after_sec=stale_after_sec
            )
        )

    counts = {"resubmit": 0, "resume_poll": 0, "needs_review": 0, "sync_orphan": 0}
    failed_events = 0
    for record in records:
        if record.terminal:
            continue
        try:
            if not record.submitted:
                # 台账里可能留着「已提交、凭证在这儿」的痕迹：进程崩在拿到上游
                # 标识之后、写业务表之前。先回填，再决定续接还是重新提交 ——
                # 不回填的话，这条记录会被判「提交结果未知」而等人核对，
                # 上游已经在跑的任务白丢。
                refreshed = await _recover_external_id(kind, record)
                if refreshed is not None:
                    record = refreshed
            if record.submitted:
                if _supports_poll(record):
                    if await _mark_running(kind, record):
                        await active.resume(kind, record.id)
                        counts["resume_poll"] += 1
                else:
                    # 只有真的写了终态才计数：`mark_failed` 落败说明这一条
                    # 已经被别人处理（取消 / 另一实例），不该算进 sync_orphan
                    if await _fail_record(
                        kind, record, SYNC_PROVIDER_ORPHAN_MESSAGE
                    ):
                        counts["sync_orphan"] += 1
                        failed_events += 1
            elif record.submit_unknown:
                # 提交结果未知：恢复阶段直接判死，不再入队等 runner 再判一次。
                # 事件与业务行同事务写 outbox，避免「记录失败、事件丢失」。
                if await _fail_record(kind, record, SUBMIT_UNKNOWN_MESSAGE):
                    counts["needs_review"] += 1
                    failed_events += 1
            else:
                # 提交前就中断了：先复位现场，再重新走 submit。不复位的话，
                # 记录可能停在 running / 旧进度，页面会一直显示「运行中 · 62%」。
                if await _reset_for_resubmit(kind, record):
                    await active.dispatch(kind, record.id)
                    counts["resubmit"] += 1
        except Exception:  # noqa: BLE001 - 单条失败不阻塞其余记录
            logger.exception("恢复失败 kind=%s id=%s", kind, record.id)

    if failed_events:
        # 恢复期已经写好 outbox；这里只做一次「尽快投递」的尝试，
        # 失败由周期投递器接着补，不影响恢复流程。
        try:
            await dispatch_pending()
        except Exception:  # noqa: BLE001
            logger.exception("恢复失败事件投递失败，等待 outbox 周期补投")

    logger.info("恢复 kind=%s：%s", kind, counts)
    return counts


async def recover_declared(specs: Sequence[object]) -> dict[str, dict[str, int]]:
    """按 `ModuleSpec.recovery_kinds` 执行框架恢复。

    参数是 `Sequence[object]` 而不是 `ModuleSpec`：本文件在 `generation` 层，
    不能反向依赖 `api` / `modules`。只读取两个属性，用 getattr 取即可。
    有 `startup` 的模块沿用自己的恢复器，框架跳过。
    """
    result: dict[str, dict[str, int]] = {}
    for spec in specs:
        kinds = getattr(spec, "recovery_kinds", ())
        startup = getattr(spec, "startup", None)
        if startup is not None and kinds:
            # 「自带恢复器 + 声明负责的 kind」是**预期组合**，留痕是为了矩阵守卫
            # 与日志都能对上（R12-07），不该按告警报——它每次启动都会响一次，
            # 只会教人忽略告警。真要说「框架接管」的那句在下面 `continue` 之后。
            logger.info(
                "模块 %s 自带恢复器，框架跳过 kind=%s（由该模块的 startup 负责）",
                getattr(spec, "name", "?"),
                ",".join(kinds),
            )
        if not kinds or startup is not None:
            continue
        for kind in kinds:
            # 启动恢复不设批次上限：旧 task worker 也是扫全部；截断会让
            # 第 101 条以后的记录一直等到下一次周期巡检才被接回。
            result[kind] = await recover_kind(kind, limit=None)
    return result


async def run_startup_recovery(
    specs: Sequence[object], *, worker_enabled: bool
) -> dict[str, dict[str, int]]:
    """启动期的唯一入口：先跑装配钩子，再按实例角色决定要不要跑恢复。

    参数语义与 `recover_declared` 相同（只读属性，不依赖 `ModuleSpec`）。

    **两个阶段，门控不同**：

    1. `bootstrap`（装配）：建内置角色、补新增权限点这类动作，任何实例都跑。
       它们幂等、不改写「正在进行的业务状态」，不跑会留下长期隐患——最典型的
       是「没开 worker 的 API 实例永远不初始化内置角色」。
    2. `startup` + 框架恢复：只在消费队列的实例上跑。

    恢复实现形状不同，但**触发条件相同**：本实例消费队列。把门控写在这一层，
    新增第三种恢复实现时不会漏；写在 `recover_declared` 里就只覆盖框架那一种
    （R13-01：`task` / `template` 恰好都自带 startup，于是两个真正需要门控的
    模块全在门控之外）。

    worker 关闭时必须连 `startup` 一起跳过，不只是「少投几个没人消费的 job」：
    模块的恢复器会**先写库再派发**——把「提交结果未知」判死、发失败通知、
    把 `started_at` 清零。这些改写不可逆，只读实例每次启动都会做一遍。
    """
    for spec in specs:
        bootstrap = getattr(spec, "bootstrap", None)
        if bootstrap is not None:
            await bootstrap()

    if not worker_enabled:
        logger.warning(
            "worker 未启用，跳过全部启动恢复（%d 个模块的 startup + 框架恢复）："
            "恢复动作会改写记录状态并把任务投进无人消费的队列；"
            "需要恢复时请开启 WORKER_ENABLED",
            len(specs),
        )
        return {}

    for spec in specs:
        startup = getattr(spec, "startup", None)
        if startup is not None:
            try:
                await startup()
            except Exception:  # noqa: BLE001
                # 恢复是「尽力而为」：某个模块的恢复器读到一条无法解读的历史行
                # （脏 configuration、旧格式记录、共享盘上一份损坏的 run.json）
                # 就不该连累其它模块，更不该让整个进程起不来。逐模块隔离 +
                # 一条能定位的日志，比「启动失败」可运维得多。
                logger.exception(
                    "模块 %s 的启动恢复失败，已跳过（其它模块继续）", getattr(spec, "name", spec)
                )
    return await recover_declared(specs)


__all__ = [
    "RECOVERY_BATCH",
    "SYNC_PROVIDER_ORPHAN_MESSAGE",
    "recover_declared",
    "recover_kind",
    "run_startup_recovery",
]
