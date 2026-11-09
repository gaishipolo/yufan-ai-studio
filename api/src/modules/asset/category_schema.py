"""分类树的出参。分类条（第②段）的数据源。

与 `/assets` 的列表出参分开：分类条要在**没有列表请求**的情况下渲染（首屏、
切换库的瞬间），如果它的数据从列表响应里挤出来，切库时就会先看到上一棵树
闪一下，再跳到新树——那种闪烁会被读成"点错了"。
"""

from __future__ import annotations

from core.schema import CamelModel

__all__ = ["AssetCategoryList", "AssetCategoryOut"]


class AssetCategoryOut(CamelModel):
    id: str
    name: str
    # `system`（收件箱与五个初始用途）/ `user`（用户自建）
    kind: str
    is_inbox: bool = False
    sort_order: int = 0
    #: 该分类下未删除、当前库可见的素材数。**口径与列表查询一致**——
    #: 角标写 149 而点进去只有 12 条，用户只会认为筛选坏了。
    count: int = 0


class AssetCategoryList(CamelModel):
    #: 回显请求的 `library`，前端据此判断"拿到的树是不是当前库的"。
    #: 切库时两个请求并发，晚到的旧响应会把界面刷回上一个库的分类。
    library: str
    items: list[AssetCategoryOut]
    #: 未分类数（收件箱角标）。单独给一份是因为"我上传了但列表里没有"是这个
    #: 产品最容易出的信任问题，它要能被单独读，不依赖前端去 items 里找。
    inbox_count: int
