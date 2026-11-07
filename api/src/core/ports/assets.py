"""「这个素材有没有被引用」的声明式注入口。

素材删除（含归档素材移入回收站）要先问一句「还有谁在用它」。答案分散在
**别的模块**手里，而业务模块之间不许互相 import —— 所以走注入口。

## 为什么必须声明，不能只靠「记得注册」

`register_*` 是 import 期副作用：新增一个把 `assets.id` 写进自己表/文件的模块，
忘了调它，删除接口照样返回成功，引用方从此裂图。这个失败方向是**静默且不可逆**的，
比「删不掉」严重得多。所以这里用三层约束：

1. **声明**：每个来源由所属模块的 `ModuleSpec.asset_reference_sources` 声明，
   框架在 `discover_modules()` 时并入「期望集合」；
2. **对账**：`ask_asset_references()` 在每次删除前检查「期望 ⊆ 已注册」，
   缺一个就抛 `Unavailable`(503)，**不返回空映射**。来源查询本身抛异常也一样失败
   ——「查不到就算没人用」永远不成立；
3. **静态守卫**：`tests/test_architecture.py` 用 AST 扫所有写入 `assets.id` 的模块，
   没声明来源的模块 CI 直接红。新增来源因此必须是改 `ModuleSpec` 的显式动作。

未装配任何模块时期望集合为空，这时删除也会被拒（fail-closed）：CLI 忘了
`discover_modules()` 会立刻暴露，而不是悄悄放过一个引用。要让某次只读查询
绕过完备性检查，显式传 `require_complete=False`。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from core.exceptions import Unavailable

__all__ = [
    "AssetReferenceChecker",
    "AssetReferenceSource",
    "ask_asset_references",
    "declare_expected_reference_sources",
    "declared_reference_sources",
    "has_asset_reference_checker",
    "reference_source_count",
    "reference_source_gaps",
    "register_asset_reference_checker",
    "bind_asset_reference_sources",
    "reset_asset_reference_checker",
]


@runtime_checkable
class AssetReferenceChecker(Protocol):
    """谁在引用这些素材。"""

    async def references(self, asset_ids: Sequence[str]) -> Mapping[str, Sequence[str]]:
        """返回 `{asset_id: [引用方描述, ...]}`，没有引用的 id 可以不出现在结果里。

        描述是给人看的（例如「封装《圣诞活动》(pack1)」），会直接进 409 的报错
        文案，所以实现方要写清楚，别只丢一个内部 id。
        """
        ...


@dataclass(frozen=True, slots=True)
class AssetReferenceSource:
    """一个模块对外声明的引用来源。

    `name` 全局唯一且稳定（写进日志与 doctor 报告，别用会变的表述）；
    `location` 指向真实的持久化位置，静态守卫拿它对账写入点。
    """

    name: str
    checker: AssetReferenceChecker
    location: str = ""
    description: str = ""


@dataclass(frozen=True, slots=True)
class _Entry:
    module: str
    checker: AssetReferenceChecker


# name -> 实际接上的实现
_sources: dict[str, _Entry] = {}
# name -> 声明它的模块（代表「必须接上」的集合）
_expected: dict[str, str] = {}


def _declare(name: str, module: str) -> None:
    """登记一个「必须接上」的来源名。同一名字被两个模块声明就是配置错误。"""
    if name in _expected and _expected[name] != module and not module.startswith("_"):
        raise ValueError(f"引用来源 {name} 被 {_expected[name]} 与 {module} 重复声明")
    _expected.setdefault(name, module)


def register_asset_reference_checker(
    port: AssetReferenceChecker, *, name: str = "", module: str = ""
) -> None:
    """兼容旧调用（测试注入假实现）的注册口。

    生产代码请用 `bind_asset_reference_sources()`。匿名来源同样登记进期望集合：
    「谁实现谁声明」在这里是安全的，因为**模块声明但没实现**这一条由
    `declare_expected_reference_sources()` 单独对账，静态完备性由 AST 守卫保证。
    """
    key = name or f"_anonymous_{id(port):x}"
    _sources[key] = _Entry(module=module or "_runtime", checker=port)
    _declare(key, module or "_runtime")


def bind_asset_reference_sources(module: str, sources: Sequence[AssetReferenceSource]) -> None:
    """模块 import 期调用：按声明的名字登记实现。

    按 `name` 去重而不是按对象身份——模块被重复 import / 发现缓存清空后重扫，
    都不该让同一个来源在聚合结果里出现两次。
    """
    for source in sources:
        if not source.name:
            raise ValueError(f"模块 {module} 声明了没有 name 的引用来源")
        _sources[source.name] = _Entry(module=module, checker=source.checker)
        _declare(source.name, module)


def declare_expected_reference_sources(specs: Sequence[object]) -> None:
    """框架在 `discover_modules()` 后调用：把 `ModuleSpec` 声明并入期望集合。

    这一步专门抓「模块声明了来源、import 期却忘了注册」：声明名进 `_expected`
    但没有对应实现，`reference_source_gaps()` 就会让删除 fail-closed。

    接收 `Sequence[object]` 而不是 `ModuleSpec`，是为了让 `core` 不必反向 import
    `api`（分层守卫）；读取的是 `name` / `asset_reference_sources` 两个鸭子类型字段。
    """
    for spec in specs:
        name = str(getattr(spec, "name", ""))
        for source in getattr(spec, "asset_reference_sources", ()) or ():
            declared = str(getattr(source, "name", ""))
            if not declared:
                raise ValueError(f"模块 {name} 声明了没有 name 的引用来源")
            _declare(declared, name)


def declared_reference_sources() -> Mapping[str, str]:
    """期望集合的只读快照，供 `app doctor` 报告。"""
    return dict(_expected)


def reference_source_gaps() -> list[str]:
    """当前不满足 fail-closed 条件的原因列表；空列表代表可以安全删除。"""
    if not _expected:
        return ["引用来源集合未知：模块尚未装配（CLI 需先 discover_modules）"]
    return [
        f"来源 {name}（应由 {module} 提供）未注册"
        for name, module in sorted(_expected.items())
        if name not in _sources
    ]


def reset_asset_reference_checker() -> None:
    """仅测试用：清空全部来源与声明。"""
    _sources.clear()
    _expected.clear()


def has_asset_reference_checker() -> bool:
    """是否有来源接上；**不代表完备**，删除判定请用 `reference_source_gaps()`。"""
    return bool(_sources)


def reference_source_count() -> int:
    """已注册来源数，自检用。"""
    return len(_sources)


async def ask_asset_references(
    asset_ids: Sequence[str], *, require_complete: bool = True
) -> Mapping[str, Sequence[str]]:
    """聚合所有来源。默认 fail-closed：来源不齐或任一来源异常，都不允许删除。

    `require_complete=False` 只给只读展示用；删除路径永远用默认值。
    """
    if require_complete:
        gaps = reference_source_gaps()
        if gaps:
            raise Unavailable(
                "引用检查不可用，拒绝删除素材：" + "；".join(gaps),
                details={"gaps": gaps},
            )
    if not _sources or not asset_ids:
        return {}
    results = await asyncio.gather(
        *(entry.checker.references(asset_ids) for entry in list(_sources.values()))
    )
    merged: dict[str, list[str]] = {}
    for result in results:
        for asset_id, descriptions in result.items():
            bucket = merged.setdefault(asset_id, [])
            for description in descriptions:
                if description not in bucket:
                    bucket.append(description)
    return {key: value for key, value in merged.items() if value}
