"""可选能力的协议化：能力探测不许用 `getattr`。

## 这份守卫在防什么

项目里「驱动/流水线可以多实现一点东西」一直靠 `getattr(x, "name", None)` 探测。
它不是契约：属性名拼错、被改名、或哪天挪到别处，探测只会**安静地**返回 `None`，
然后退化成默认行为——而症状离原因很远：

- `persist_output` 改名 → 产物不落盘 → 几小时后前端开始裂图；
- `archive_request` 改名 → 生成完了，草稿箱里一条都没有；
- `stats` 改名 → 监控指标与健康检查的队列计数**整段消失**；
- `supports_poll` 改名 → 同步供应商被当成异步去续查。

协议把这四类「静默降级」变成可检查的：调用方 `isinstance`，改名会**立刻失配**，
而不是悄悄地不发生。

## 为什么不写「扫所有 getattr」

`getattr` 本身不是坏事——读 `route.path`、读 ORM 行的 `rowcount`、读
`ModuleSpec` 的可选字段，都是正当用法。所以这里只盯**这 6 个能力名**：
它们已经有协议了，不许再退回 `getattr` 探测。
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from typing import Any

import pytest

from generation.pipeline import (
    ArchiveRequestable,
    FailOnModelGoneDeclared,
    Persistable,
    StrictModelDeclared,
)
from infra.ai.base import PollCapable
from infra.queue.base import StatsCapable

SRC = Path(__file__).resolve().parents[1] / "src"

#: 已协议化的能力名 → 不许再用 getattr 探测。
PROTOCOLIZED_CAPABILITIES: frozenset[str] = frozenset(
    {
        "persist_output",
        "archive_request",
        "stats",
        "supports_poll",
        "strict_model",
        "fail_on_model_gone",
    }
)


def _python_files() -> list[Path]:
    return sorted(
        path
        for path in SRC.rglob("*.py")
        if "__pycache__" not in path.parts and "scaffold" not in path.parts
    )


def _relative(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def _probes_in_source(source: str, label: str = "<src>") -> list[str]:
    """在一段源码里找出「用 getattr 探测已协议化能力」的位置。

    守卫 1 与它的自检共用这个函数——自检喂一段**故意违规**的源码，
    证明这条规则真的会红（否则守卫可能一直是绿的，因为规则本身写错了）。
    """
    offenders: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "getattr"):
            continue
        if len(node.args) < 2:
            continue
        name_node = node.args[1]
        if not isinstance(name_node, ast.Constant):
            continue
        if not isinstance(name_node.value, str):
            continue
        if name_node.value not in PROTOCOLIZED_CAPABILITIES:
            continue
        offenders.append(f"{label}:{node.lineno}: {name_node.value}")
    return offenders


# --------------------------------------------------------------------------- #
# 守卫 1：已协议化的能力不许再退回 getattr 探测
# --------------------------------------------------------------------------- #
def test_protocolized_capabilities_are_not_probed_with_getattr() -> None:
    """扫源码：不允许 `getattr(x, "<已协议化的能力名>", ...)`。

    这条是本守卫的主干。它不禁止 `getattr` 本身（读 `route.path` 之类正当），
    只禁止**已经立了协议**的能力再被探测——因为那种写法正是「改名即静默降级」。
    """
    offenders: list[str] = []
    for path in _python_files():
        offenders.extend(
            _probes_in_source(path.read_text(encoding="utf-8"), _relative(path))
        )
    assert not offenders, (
        "这些能力已经有协议了，不许再用 getattr 探测（改名会静默降级）-> "
        + "; ".join(offenders)
        + "；改用对应的 Protocol + isinstance"
    )


def test_the_getattr_guard_actually_catches_violations() -> None:
    """自检：故意违规的写法必须被守卫抓到。

    没有这一条，「守卫是绿的」有两种可能——真的干净，或者规则写错了。
    这里用三种违规写法 + 一种正当写法把两种可能区分开。
    """
    violating = """
    persist = getattr(pipeline, "persist_output", None)
    stats = getattr(queue, "stats", None)
    flag = getattr(provider, "supports_poll", False)
    """
    caught = _probes_in_source(textwrap.dedent(violating))
    assert len(caught) == 3, f"应该抓到 3 处，实际 {caught}"

    legitimate = """
    path = getattr(route, "path", None)
    count = getattr(row, "rowcount", 0)
    name = getattr(obj, field_name, None)
    """
    assert _probes_in_source(textwrap.dedent(legitimate)) == [], "正当的 getattr 被误报了"


# --------------------------------------------------------------------------- #
# 守卫 2：协议本身的行为（自检）
# --------------------------------------------------------------------------- #
class _WithPersist:
    async def persist_output(self, record_id: str, url: str) -> str:
        return f"{record_id}:{url}"


class _WithoutPersist:
    pass


class _WithStrict:
    strict_model = True


class _WithStrictFalse:
    strict_model = False


class _WithStats:
    async def stats(self) -> dict[str, int]:
        return {"pending": 0}


class _WithPoll:
    supports_poll = True


def test_protocols_detect_capability_presence() -> None:
    """协议只认「有没有声明」，不认值——值的语义由调用方决定。

    这一条是自检：把协议的判定行为钉住，将来有人改成别的机制时，
    这里会先红。
    """
    assert isinstance(_WithPersist(), Persistable)
    assert not isinstance(_WithoutPersist(), Persistable)

    assert isinstance(_WithStrict(), StrictModelDeclared)
    assert isinstance(_WithStrictFalse(), StrictModelDeclared)
    assert not isinstance(_WithoutPersist(), StrictModelDeclared)

    assert isinstance(_WithStats(), StatsCapable)
    assert not isinstance(_WithoutPersist(), StatsCapable)

    assert isinstance(_WithPoll(), PollCapable)
    assert not isinstance(_WithoutPersist(), PollCapable)


def test_archive_and_fail_on_model_gone_protocols_detect_declaration() -> None:
    """另两个协议同样只认「有没有声明」。"""

    class _WithArchive:
        async def archive_request(self, session: Any, record: Any, stored: str) -> Any:
            return None

    class _WithFailOnGone:
        fail_on_model_gone = False

    assert isinstance(_WithArchive(), ArchiveRequestable)
    assert not isinstance(_WithoutPersist(), ArchiveRequestable)
    assert isinstance(_WithFailOnGone(), FailOnModelGoneDeclared)
    assert not isinstance(_WithoutPersist(), FailOnModelGoneDeclared)


# --------------------------------------------------------------------------- #
# 守卫 3：真实实现与协议对得上
# --------------------------------------------------------------------------- #
def test_real_queue_drivers_satisfy_stats_capable() -> None:
    """两个现役队列驱动都实现了 `stats()`——协议与实现不许漂移。

    以前 `db.py` 的注释写着「不在协议里（别的驱动没有这个概念）」，而
    `memory.py` 其实也有。那句注释让调用方改用 getattr 探测，
    于是「驱动有没有自报计数」变成了猜。这条守卫让漂移当场暴露。
    """
    from infra.queue.db import DbJobQueue
    from infra.queue.memory import MemoryJobQueue

    for driver in (MemoryJobQueue(), DbJobQueue()):
        assert isinstance(driver, StatsCapable), (
            f"{type(driver).__name__} 实现了 stats()，却没满足 StatsCapable 协议"
        )


def test_real_pipelines_satisfy_their_optional_protocols() -> None:
    """已注册流水线若声明了某个可选能力，就必须满足对应协议。

    协议写错了名字（比如把 `persist_output` 写成 `persistoutput`）时，
    这里会立刻发现：实现有方法，但协议检查不过。
    """
    from generation.pipeline import get_pipeline, pipeline_kinds

    checked = 0
    for kind in pipeline_kinds():
        pipeline = get_pipeline(kind)
        if hasattr(pipeline, "persist_output"):
            assert isinstance(pipeline, Persistable), (
                f"pipeline {kind} 有 persist_output，但不满足 Persistable 协议"
            )
            checked += 1
        if hasattr(pipeline, "archive_request"):
            assert isinstance(pipeline, ArchiveRequestable), (
                f"pipeline {kind} 有 archive_request，但不满足 ArchiveRequestable 协议"
            )
            checked += 1
    assert checked > 0, "没有任何流水线声明可选能力，这条守卫等于空转"


@pytest.mark.parametrize(
    "capability",
    sorted(PROTOCOLIZED_CAPABILITIES),
)
def test_every_protocolized_capability_has_a_guard(capability: str) -> None:
    """每个被协议化的能力名都要真的出现在源码里（守卫不是空转）。

    某个能力名从源码里消失了（比如钩子整体下线了），说明这条规则该退休了，
    而不是留着一份永远匹配不到的白名单。
    """
    hits = 0
    for path in _python_files():
        if capability in path.read_text(encoding="utf-8"):
            hits += 1
    assert hits > 0, f"能力 {capability} 在源码里已经不存在了，白名单该清理"
