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
| 无 `external_id`，`submit_attempts>0` | 交给 runner 判死 | 防重复计费：宁可等人核对，不自动重提 |
| 无 `external_id`，`attempts=0` | `runner.dispatch()` | 建了记录却没进队列（进程内队列重启即丢） |

## 模块要提供什么

一个可选的 `pipeline.list_unfinished(session, limit)`，返回未终态的
`GenerationRecord` 列表。框架不认识任何 ORM 实体，差异仍由各模块吸收。

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

from core.events import get_event_bus
from core.logging import get_logger
from db.session import get_session_factory
from generation.events import EVT_GENERATION_FAILED
from generation.pipeline import get_pipeline
from generation.runner import GenerationRunner, get_runner
from generation.spec import GenerationRecord
from infra import get_model_router

logger = get_logger(__name__)

RECOVERY_BATCH = 100

SYNC_PROVIDER_ORPHAN_MESSAGE = (
    "生成结果未能落库：该供应商是同步接口，不支持续查；本次提交可能已产生费用，"
    "请凭供应商任务号（externalId）核对结果后再决定是否重试"
)


@runtime_checkable
class RecoverablePipeline(Protocol):
    async def list_unfinished(
        self, session: AsyncSession, limit: int
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
    return bool(getattr(provider, "supports_poll", False))


async def _fail_unrecoverable(kind: str, record: GenerationRecord) -> None:
    """同步供应商留下的 running 孤儿：无法续查，显式置失败并补发事件。"""
    message = SYNC_PROVIDER_ORPHAN_MESSAGE
    async with get_session_factory()() as session:
        store = get_pipeline(kind).store(session)
        current = await store.load(record.id)
        if current is None or current.terminal:
            return
        await store.mark_failed(record.id, message)
        await session.commit()
    await get_event_bus().publish(
        EVT_GENERATION_FAILED,
        kind=kind,
        record_id=record.id,
        owner_id=record.owner_id,
        error=message,
    )


async def recover_kind(
    kind: str,
    *,
    runner: GenerationRunner | None = None,
    limit: int = RECOVERY_BATCH,
) -> dict[str, int]:
    """恢复某一类生成能力的未终态记录，返回各类处置的条数。"""
    pipeline = get_pipeline(kind)
    if not isinstance(pipeline, RecoverablePipeline):
        raise RuntimeError(
            f"kind={kind!r} 声明了框架恢复但流水线没有 list_unfinished(session, limit)；"
            "要么实现它，要么改用模块自己的 startup"
        )

    active = runner or get_runner()
    async with get_session_factory()() as session:
        records = list(await pipeline.list_unfinished(session, limit))

    counts = {"resubmit": 0, "resume_poll": 0, "needs_review": 0, "sync_orphan": 0}
    for record in records:
        if record.terminal:
            continue
        try:
            if record.submitted:
                if _supports_poll(record):
                    await active.resume(kind, record.id)
                    counts["resume_poll"] += 1
                else:
                    await _fail_unrecoverable(kind, record)
                    counts["sync_orphan"] += 1
            else:
                # submit_attempts>0 的记录也会被 dispatch，但 runner 的
                # 「提交结果未知」分支会把它置为 failed 等人核对，不会二次提交。
                await active.dispatch(kind, record.id)
                key = "needs_review" if record.submit_unknown else "resubmit"
                counts[key] += 1
        except Exception:  # noqa: BLE001 - 单条失败不阻塞其余记录
            logger.exception("恢复失败 kind=%s id=%s", kind, record.id)

    logger.info("启动恢复 kind=%s：%s", kind, counts)
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
            # 双声明不是错误（旧模块自有恢复器），但必须留痕：否则作者以为
            # 框架接管了这些 kind，实际声明被静默跳过，矩阵守卫却照常绿（R12-07）。
            logger.warning(
                "模块 %s 同时声明 startup 与 recovery_kinds=%s：框架恢复跳过，"
                "这些 kind 由模块自己的 startup 负责",
                getattr(spec, "name", "?"),
                ",".join(kinds),
            )
        if not kinds or startup is not None:
            continue
        for kind in kinds:
            result[kind] = await recover_kind(kind)
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
            await startup()
    return await recover_declared(specs)


__all__ = [
    "RECOVERY_BATCH",
    "SYNC_PROVIDER_ORPHAN_MESSAGE",
    "recover_declared",
    "recover_kind",
    "run_startup_recovery",
]
