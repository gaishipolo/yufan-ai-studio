"""提交前的最终授权：原片还可用、封装还已发布、档案还能提交。

## 这道闸为什么在业务层

它原来在 `infra/ai/libtv_process.run_node` 的 `submit` 阶段，调用
`MediaCatalog.check_run(run)`。那里执行器是个独立子进程，手里只有归档盘和
`run.json`，所以要为「原片被删了但档案还在」专门造一份 JSON 镜像去比对。
2026-11-08 随模板库重建搬到业务层：这里能直接查库，判断与真正花钱的时刻之间
不再隔着一份可能过期的镜像。

## 为什么是两个调用点而不是一个

`ReplacementService.execute()` 在**排队前**查一次，让用户当场得到「封装已停用」
的提示，而不是等几十秒后看到任务失败。但排队到真正提交之间还隔着 upload
阶段（超时 1500 秒），期间封装被停用、原片被删都完全可能——所以
`worker.prepare_upload` 在 upload 前与 dispatch（提交）前各再查一次。

三处用的是同一个函数，不是三份规则。旧实现在执行器里做的正是「提交前最后一次」，
这一版把它放回业务层的同一时刻，窗口反而比「只有排队前那一次」更小。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from core.ports.media import get_asset_lookup
from modules.template.models import ReplacementRun
from modules.template.repository import PackRepository, RunRepository

__all__ = ["authorization_gap"]


async def authorization_gap(session: AsyncSession, run: ReplacementRun) -> str | None:
    """返回「不能提交」的原因；`None` 表示仍可用。

    返回字符串而不是抛异常，是因为同一个判断要服务两类调用方：HTTP 入口
    （`execute`，转成 `BadRequest` 给用户看）和队列里的作业（`prepare_upload`，
    把原因写进 `last_error` 并停住）。抛异常会让作业侧多写一层 try/except，
    而那个 except 一旦写漏，任务就会带着失效的授权继续跑到提交。
    """
    if run.pack_id and run.pack_version:
        packs = PackRepository(session)
        frozen = await packs.get_version(run.pack_id, run.pack_version)
        pack = await packs.get(run.pack_id)
        # 三种失效都归到一句话：冻结版本没了（历史数据缺行）、封装被删、
        # 封装被停用。对外不区分，用户要做的动作都是「重新选一个封装」。
        if frozen is None or pack is None or pack.deleted_at is not None:
            return "素材包已停用，请重新选择后准备"
        if pack.status != "published":
            return "素材包已停用，请重新选择后准备"
    rows = await RunRepository(session).inputs(run.id)
    if rows:
        media = await get_asset_lookup().get_many(
            session, [row.asset_id for row in rows]
        )
        # 两个条件都要：`get_many` 不排除软删，行还在、`deleted_at` 已置位——
        # 只看「键在不在」会让删掉的素材照样通过（原来那句判断永远不成立）。
        gone = [
            row.asset_id
            for row in rows
            if row.asset_id not in media or media[row.asset_id].deleted_at is not None
        ]
        if gone:
            return "本次输入的素材已被删除，请重新准备"
    return None
