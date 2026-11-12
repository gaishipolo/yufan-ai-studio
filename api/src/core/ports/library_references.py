"""v2 资产删除前的引用检查注入口（声明式 + fail-closed）。

v2 的「彻底删除」不可逆，删之前必须问一遍「还有谁在用它」。答案分散在
模板、任务、封装、下载任务手里，而业务模块之间不许互相 import——所以走注入口。

## 为什么必须声明，不能只靠「记得注册」

与 `core/ports/assets.py` 同一类失败方向：漏注册 → 引用检查返回空 →
删除照常成功 → 引用方从此裂图/裂视频。静默且不可逆。所以这里用三层约束：

1. **声明**：每个来源由所属模块的 `ModuleSpec.library_reference_sources` 声明，
   `discover_modules()` 时并入「期望集合」；
2. **对账**：`ask_library_references()` 在每次删除前检查「期望 ⊆ 已注册」，
   缺一个就抛 `Unavailable`(503)，**不返回空映射**；任一来源查询抛异常同样失败
   ——「查不到就算没人用」永远不成立；
3. **静态守卫**：`tests/test_architecture.py` 用 AST 扫所有写 `library_asset_id`
   的模块，没声明来源的模块 CI 直接红。新增来源因此必须是改 `ModuleSpec` 的
   显式动作。

未装配任何模块时期望集合为空，这时删除也会被拒（fail-closed）：CLI 忘了
`discover_modules()` 会立刻暴露，而不是悄悄放过一个引用。要让某次只读查询
绕过完备性检查，显式传 `require_complete=False`。

与旧 `asset-references` 的区别：那个查 `assets.id` 的引用（`video_tasks`、
`template_pack_references`），这个查 `library_asset_id` 的引用（v2 封装成员、
v2 下载任务、以及将来接入的模块）。两套 id 空间不同，不能混用一个注入口。

## 为什么不并发聚合

v1 用 `asyncio.gather` 是因为那些 checker 自己开连接；这里的 checker 签名带
`session`，用的是**调用方的同一个事务**。`AsyncSession` 不支持并发 execute，
所以逐条串行——需要聚合几十个来源的场景不存在，多一次串行往返换「能进同一个
事务」是划算的。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import Unavailable

__all__ = [
    "LibraryReferenceChecker",
    "LibraryReferenceSource",
    "ask_library_references",
    "bind_library_reference_sources",
    "declare_expected_library_reference_sources",
    "declared_library_reference_sources",
    "has_library_references",
    "library_reference_count",
    "library_reference_gaps",
    "library_references_ready",
    "register_library_references",
    "require_library_references",
    "reset_library_references",
]


@runtime_checkable
class LibraryReferenceChecker(Protocol):
    """谁在引用这些 v2 资产。"""

    async def references(
        self, session: AsyncSession, asset_ids: Sequence[str]
    ) -> Mapping[str, Sequence[str]]:
        """返回 `{asset_id: [给人看的引用方描述]}`，无引用的 id 可省略。

        描述会直接进 409 的文案（例如「封装《圣诞活动》」「下载任务 d_42」），
        所以写清楚，别只丢内部 id。
        """
        ...


@dataclass(frozen=True, slots=True)
class LibraryReferenceSource:
    """一个模块对外声明的引用来源。

    `name` 全局唯一且稳定（写进日志与 doctor 报告，别用会变的表述）；
    `location` 指向真实的持久化位置（`文件:符号`），静态守卫拿它对账写入点。
    """

    name: str
    checker: LibraryReferenceChecker
    location: str = ""
    description: str = ""


@dataclass(frozen=True, slots=True)
class _Entry:
    module: str
    checker: LibraryReferenceChecker


# name -> 实际接上的实现
_sources: dict[str, _Entry] = {}
# name -> 声明它的模块（代表「必须接上」的集合）
_expected: dict[str, str] = {}


def _declare(name: str, module: str) -> None:
    """登记一个「必须接上」的来源名。同一名字被两个模块声明就是配置错误。"""
    if name in _expected and _expected[name] != module and not module.startswith("_"):
        raise ValueError(f"引用来源 {name} 被 {_expected[name]} 与 {module} 重复声明")
    _expected.setdefault(name, module)


def register_library_references(
    port: LibraryReferenceChecker, *, name: str = "", module: str = ""
) -> LibraryReferenceChecker:
    """兼容旧调用（单例注册 / 测试注入假实现）的注册口。

    生产代码请用 `bind_library_reference_sources()`。匿名来源同样登记进期望集合：
    「谁实现谁声明」在这里是安全的，因为**模块声明但没实现**这一条由
    `declare_expected_library_reference_sources()` 单独对账，静态完备性由 AST 守卫保证。
    """
    key = name or f"_anonymous_{id(port):x}"
    _sources[key] = _Entry(module=module or "_runtime", checker=port)
    _declare(key, module or "_runtime")
    return port


def bind_library_reference_sources(
    module: str, sources: Sequence[LibraryReferenceSource]
) -> None:
    """模块 import 期调用：按声明的名字登记实现。

    按 `name` 去重而不是按对象身份——模块被重复 import / 发现缓存清空后重扫，
    都不该让同一个来源在聚合结果里出现两次。
    """
    for source in sources:
        if not source.name:
            raise ValueError(f"模块 {module} 声明了没有 name 的引用来源")
        _sources[source.name] = _Entry(module=module, checker=source.checker)
        _declare(source.name, module)


def declare_expected_library_reference_sources(specs: Sequence[object]) -> None:
    """框架在 `discover_modules()` 后调用：把 `ModuleSpec` 声明并入期望集合。

    这一步专门抓「模块声明了来源、import 期却忘了注册」：声明名进 `_expected`
    但没有对应实现，`library_reference_gaps()` 就会让删除 fail-closed。

    接收 `Sequence[object]` 而不是 `ModuleSpec`，是为了让 `core` 不必反向 import
    `api`（分层守卫）；读取的是 `name` / `library_reference_sources` 两个鸭子类型字段。
    """
    for spec in specs:
        module = str(getattr(spec, "name", ""))
        for source in getattr(spec, "library_reference_sources", ()) or ():
            declared = str(getattr(source, "name", ""))
            if not declared:
                raise ValueError(f"模块 {module} 声明了没有 name 的引用来源")
            _declare(declared, module)


def declared_library_reference_sources() -> Mapping[str, str]:
    """期望集合的只读快照，供 `app doctor` 报告。"""
    return dict(_expected)


def library_reference_gaps() -> list[str]:
    """当前不满足 fail-closed 条件的原因列表；空列表代表可以安全删除。"""
    if not _expected:
        return ["引用来源集合未知：模块尚未装配（CLI 需先 discover_modules）"]
    return [
        f"来源 {name}（应由 {module} 提供）未注册"
        for name, module in sorted(_expected.items())
        if name not in _sources
    ]


def library_references_ready() -> bool:
    """声明与实现是否对齐。`PORT_CATALOG` 与详情页的「可判定」判据。

    不用 `has_library_references()`：那个只回答「有没有人注册」，注册了但漏声明
    一样会让某个来源被静默忽略。
    """
    return not library_reference_gaps()


def has_library_references() -> bool:
    """是否有来源接上；**不代表完备**，删除判定请用 `library_reference_gaps()`。"""
    return bool(_sources)


def library_reference_count() -> int:
    """已注册来源数，自检用。"""
    return len(_sources)


def reset_library_references() -> None:
    """仅测试用：清空全部来源与声明。"""
    _sources.clear()
    _expected.clear()


async def ask_library_references(
    session: AsyncSession,
    asset_ids: Sequence[str],
    *,
    require_complete: bool = True,
) -> Mapping[str, Sequence[str]]:
    """聚合所有来源。默认 fail-closed：来源不齐或任一来源异常，都不允许删除。

    `require_complete=False` 只给只读展示用；删除路径永远用默认值。
    """
    if require_complete:
        require_library_references()
    if not _sources or not asset_ids:
        return {}
    merged: dict[str, list[str]] = {}
    # 串行而非 gather：checker 用的是调用方的 session，同一个 AsyncSession
    # 不能并发 execute（见模块 docstring 的「为什么不并发聚合」）。
    for entry in list(_sources.values()):
        result = await entry.checker.references(session, asset_ids)
        for asset_id, descriptions in result.items():
            bucket = merged.setdefault(asset_id, [])
            for description in descriptions:
                if description not in bucket:
                    bucket.append(description)
    return {key: value for key, value in merged.items() if value}


def require_library_references() -> None:
    """删除前的同步前置断言：来源不齐就抛 503。

    单独抽出来是给「不走 `ask_library_references()` 也要拒绝删除」的调用点用的
    （例如只想知道「现在能不能判」的地方），同时让 fail-closed 的文案只有一份。
    """
    gaps = library_reference_gaps()
    if gaps:
        raise Unavailable(
            "资产引用检查不可用，拒绝彻底删除：" + "；".join(gaps),
            details={"gaps": gaps},
        )
