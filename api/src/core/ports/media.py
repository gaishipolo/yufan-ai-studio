"""「这些素材现在还能不能用」的只读注入口。

模板库在 prepare 阶段要回答两类问题，答案都在 `assets` 表里：

1. **这张图是谁的、什么分类、删了没有** —— 决定它能不能进封装、能不能当参考图；
2. **这个原片还能不能当输入** —— 自己上传的、或别人共享出来的公共原片。

这些问题的答案在素材模块的手里，而业务模块之间不许互相 import，所以走注入口。
与 `core.ports.assets` 的分工：那个文件回答「谁还在引用它」（删除前拦截），
这个文件回答「它本身是什么」（使用前校验）。两件事的失败方向相反——
引用检查宁可误拒，查询检查必须准确。

## fail-open 还是 fail-closed

这里选 **fail-closed**：没注册实现时抛 `Unavailable`(503)，不返回 `None`。
理由与引用检查相同——「查不到」被当成「不存在」时，用户看到的是
「素材不存在或归属不符」这种把配置故障说成数据问题的报错，排查方向会被带偏。

这与 `api/ports.py` 的「未注册必须返回中性值」相反，是**刻意的例外**（与
`asset-references` 同类），理由已写进 `PORT_CATALOG` 的 `when_unregistered`。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import Unavailable
from core.media import StoredMedia
from core.ports.host import Port

__all__ = [
    "AssetLookup",
    "get_asset_lookup",
    "has_asset_lookup",
    "register_asset_lookup",
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


_lookup: Port[AssetLookup] = Port("asset-lookup", multi=False)


def register_asset_lookup(port: AssetLookup) -> AssetLookup:
    """素材模块在 import 期调用一次。

    名字是 `register_` 而不是 `bind_`。在这个仓里 `bind_` 是「声明一个来源」的
    语义（`bind_asset_reference_sources`：只登记名字，不注册实现），而这里注册的
    是实现本身。改名不只是对齐词表——`tests/test_architecture.py` 的端口守卫按
    `register_` / `set_` 前缀识别注入口，写成 `bind_` 之后这个文件既不被守卫当作
    注入口扫描、也没进 `PORT_CATALOG`，于是「素材模块没装配」时启动自检与
    `app modules` 都一片绿，只有真正调用 prepare 才拿到 503。
    """
    return _lookup.provide(port)


def has_asset_lookup() -> bool:
    return _lookup.has()


def get_asset_lookup() -> AssetLookup:
    impl = _lookup.single()
    if impl is None:
        raise Unavailable(
            "素材查询不可用：没有任何模块注册 AssetLookup。"
            "通常是启动时漏了 `discover_modules()`。"
        )
    return impl


def reset_asset_lookup() -> None:
    """仅测试用。"""
    _lookup.reset()
