"""素材 id 属于哪一套空间 —— dual-read 兼容期显式分流的唯一依据。

## 为什么需要它

v1 `assets.id` 与 v2 `library_assets.id` 都是 32 位 uuid hex（`db/base.py::UUIDMixin`），
**形状上完全不可区分**。切换读路径之后，`template_pack_references.asset_id` 里的历史行
仍然指向 v1，新行指向 v2；同一列混着两套 id。

`core/ports/library_lookup.py:8-10` 明确禁止「先查 v2、查不到回退 v1」的隐式兜底——
那会让某一侧悄悄读到另一半数据，而且只在删除之后才暴露成裂图。所以必须**显式**标记，
并且按标记分流到 `get_asset_lookup()` 或 `get_library_lookup()`。

## 为什么默认值是「当前空间」而不是硬编码 v1

标记列在写入时必须落一个值，否则漏传就是 NULL（SQLite/PG 都不报错，读到才炸）。
默认值取 `CURRENT_ASSET_ID_SPACE`：**新写入的行自动落在当前空间**，历史行的值已经
落盘、不受影响。步 2.5c 切换读写路径时只改下面这一行常量，不用逐个 INSERT 点改口径
——单点，且改错了只有一处。

历史行由迁移 `backfill` 显式写成 `v1`（`NULL` 不允许，见迁移里的三步）。

## 为什么不用布尔列

`is_v2` / `legacy` 这类命名会诱导出「`not is_v2` 就是 v1」的推断，而将来可能有第三套
（例如导入器带来的外部 id）。字符串空间名可扩展，且能原样写进日志与报错文案。
"""

from __future__ import annotations

__all__ = [
    "ASSET_ID_SPACE_V1",
    "ASSET_ID_SPACE_V2",
    "CURRENT_ASSET_ID_SPACE",
    "asset_id_space_default",
]

#: 旧 `assets` 表的 id 空间。历史行全部落在这里。
ASSET_ID_SPACE_V1 = "v1"
#: v2 `library_assets` 表的 id 空间。
ASSET_ID_SPACE_V2 = "v2"

#: 新写入的行该记哪个空间。**步 2.5c 切换读写路径时改这一行。**
CURRENT_ASSET_ID_SPACE = ASSET_ID_SPACE_V1


def asset_id_space_default() -> str:
    """`mapped_column(default=...)` 用。

    刻意是函数而不是直接传常量：常量在模块 import 时就固化了，而这里要的是
    **每次 INSERT 时**读当前值——否则同一进程内切换（测试、灰度开关）不生效，
    而「改一行常量」这个承诺就不成立了。
    """
    return CURRENT_ASSET_ID_SPACE
