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

from core.context import CurrentUser
from core.ports.library_lookup import LibraryAssetKind, LibraryAssetState
from modules.template.asset_source import resolve_assets
from modules.template.models import ReplacementRun, TemplatePack, TemplatePackVersion
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
        configuration = run.configuration if isinstance(run.configuration, dict) else {}
        extras = configuration.get("extra_pack_ids", [])
        versions = configuration.get("extra_pack_versions", [])
        for index, pack_id in enumerate(extras):
            extra = await packs.get(pack_id)
            if (
                extra is None
                or extra.deleted_at is not None
                or extra.status != "published"
                or extra.kind != run.kind
            ):
                return f"第{index + 2}位人物素材包已停用，请重新选择后准备"
            if index < len(versions) and await packs.get_version(pack_id, versions[index]) is None:
                return "额外人物包的发布版本已不可用，请重新准备"
    rows = await RunRepository(session).inputs(run.id)
    configuration = run.configuration if isinstance(run.configuration, dict) else {}
    fixed_refs = configuration.get("fixed_references", [])
    actor = CurrentUser(id=run.owner_id, name=run.owner_id)
    packs = PackRepository(session)
    validated: list[tuple[dict[str, object], TemplatePack, TemplatePackVersion]] = []
    for ref in fixed_refs:
        pack = await packs.get(ref["packId"])
        frozen = await packs.get_version(ref["packId"], ref["packVersion"])
        if (pack is None or pack.deleted_at or pack.status != "published" or frozen is None
                or ref["assetId"] not in frozen.reference_ids):
            return "固定引用的素材包已停用或版本不可用，请重新准备"
        validated.append((ref, pack, frozen))

    fixed_assets = await resolve_assets(
        session,
        [str(ref["assetId"]) for ref, _pack, _frozen in validated],
        actor=actor,
    )
    for ref, _pack, _frozen in validated:
        asset_id = str(ref["assetId"])
        asset_v2 = fixed_assets.v2.get(asset_id)
        if (
            asset_v2 is None
            or asset_v2.state != LibraryAssetState.ACTIVE
            or asset_v2.kind != LibraryAssetKind.IMAGE
            or asset_id not in fixed_assets.readable_v2
        ):
            return "固定引用的参考图已不可用，请重新准备"

    input_assets = await resolve_assets(
        session,
        [row.asset_id for row in rows],
        actor=actor,
    )
    gone: list[str] = []
    for row in rows:
        asset_v2 = input_assets.v2.get(row.asset_id)
        if asset_v2 is None or asset_v2.state != LibraryAssetState.ACTIVE:
            gone.append(row.asset_id)
    if gone:
        return "本次输入的素材已被删除，请重新准备"

    for row in rows:
        asset_v2 = input_assets.v2[row.asset_id]
        if row.role == "source_video":
            if (
                asset_v2.kind != LibraryAssetKind.VIDEO
                or row.asset_id not in input_assets.readable_v2
            ):
                return "原片不存在或已停止共享"
        elif (
            asset_v2.kind != LibraryAssetKind.IMAGE
            or row.asset_id not in input_assets.readable_v2
        ):
            return "本次输入的素材已被删除，请重新准备"
    return None
