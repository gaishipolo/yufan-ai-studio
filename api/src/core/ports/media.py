"""「这些素材现在还能不能用」的只读注入口。

模板库在 prepare 阶段要回答两类问题，答案都在 `assets` 表里：

1. **这张图是谁的、什么分类、删了没有** —— 决定它能不能进封装、能不能当参考图；
2. **这个原片还能不能当输入** —— 自己上传的、或别人共享出来的公共原片。

这些问题的答案在素材模块的手里，而业务模块之间不许互相 import，所以走注入口。
与 `core.ports.assets` 的分工：那个文件回答「谁还在引用它」（删除前拦截），
这个文件回答「它本身是什么」（使用前校验）。两件事的失败方向相反——
引用检查宁可误拒，查询检查必须准确。

## fail-open 还是 fail-closed

这里选 **fail-closed**：没注册实现时抛 `Unavailable`(503)，不返回 None。
理由与引用检查相同——「查不到」被当成「不存在」时，用户看到的是
「素材不存在或归属不符」这种把配置故障说成数据问题的报错，排查方向会被带偏。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import Unavailable
from core.media import StoredMedia

__all__ = [
    "AssetLookup",
    "bind_asset_lookup",
    "get_asset_lookup",
    "has_asset_lookup",
    "reset_asset_lookup",
]


@runtime_checkable
class AssetLookup(Protocol):
    """按 id 批量取素材元数据。"""

    async def get_many(
        self, session: AsyncSession, asset_ids: Sequence[str]
    ) -> dict[str, StoredMedia]:
        """返回 `{asset_id: StoredMedia}`，**不存在的 id 不出现在结果里**。

        实现方不要为缺失的 id 造占位对象：调用方靠「键是否存在」判断素材没了，
        返回一个字段全空的替身会让「已删除」和「还没上传完」混成一种情况。

        `session` 由调用方传入，不是实现方自己开：调用方在同一个事务里先读
        封装、再读素材，两次读必须看到同一个快照，否则会出现「校验通过、
        提交时素材已被别人删掉」的窗口。
        """
        ...


_impl: AssetLookup | None = None


def bind_asset_lookup(port: AssetLookup) -> None:
    """素材模块在 import 期调用一次。重复调用以最后一次为准（测试注入假实现）。"""
    global _impl
    _impl = port


def has_asset_lookup() -> bool:
    return _impl is not None


def get_asset_lookup() -> AssetLookup:
    if _impl is None:
        raise Unavailable(
            "素材查询不可用：没有任何模块注册 AssetLookup。"
            "通常是启动时漏了 `discover_modules()`。"
        )
    return _impl


def reset_asset_lookup() -> None:
    """仅测试用。"""
    global _impl
    _impl = None
