"""摘掉产品素材包里的「产品细节图」，让包只保留正面主图与三视图。

## 为什么要这么做

细节图在提示词里从不被引用（`{{Mixed 4}}` 只上传、不出现），却仍然占用
执行器的参考图额度，并且它的局部特写会跟主图/三视图抢「整体外观」的解释权。
产品替换只需要两张图：**正面主图定外观、三视图定结构**。

## 为什么走「保存草稿 → 发布新版本」而不是改表

已发布版本是冻结证据（`template_pack_versions` 只增不改），直接删
`template_pack_references` 会让「v1 快照里还有细节图、head 里没有」这件事
失去留痕。走 service 层的 `save` + `publish` 才能同时拿到三样东西：
`gaps()` 的补项校验、`_verify_bytes()` 的字节校验、以及一条可回退的新版本。

运行时读的是 `payload.pack_version`（前端取的是**当前最新版本**），所以发布
v(n+1) 之后，所有已封装的视频模板都会自动按「两张图」跑——模板只声明用途
（front / multiview），不绑定具体 asset，这正是当初设计成按用途取图的原因。

## 用法（在 api/ 目录下）

    python scripts/prune_product_pack_detail.py                  # 只读预览
    python scripts/prune_product_pack_detail.py --apply          # 保存并发布
    python scripts/prune_product_pack_detail.py --pack-id <id>   # 只处理一个包
    python scripts/prune_product_pack_detail.py --rollback <snapshot.json>

`--apply` 前会把变更前的完整状态写到 `var/backup/`，`--rollback` 用它复原。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402

import db.audit_sink  # noqa: E402,F401  发布留痕的写入端，import 即注册
import modules.asset.lookup  # noqa: E402,F401  素材元数据注入口，import 即注册
from core import audit  # noqa: E402
from db.session import get_session_factory  # noqa: E402
from modules.template.models import TemplatePack  # noqa: E402
from modules.template.pack_service import PackService  # noqa: E402
from modules.template.repository import PackRepository  # noqa: E402
from modules.template.schema import PackBinding, PackDraftIn  # noqa: E402

#: 要摘掉的用途。只摘 `detail`：`back`/`side` 是结构图，和细节图不是一回事。
DROP_ROLES = ("detail",)
#: 摘完后期望留下的用途。顺序必须与用途表一致（front 在前），
#: 否则提示词里的 `{{Mixed 2}}` / `{{Mixed 3}}` 会互换。
KEEP_ROLES = ("front", "multiview")

BACKUP_DIR = Path(__file__).resolve().parents[1] / "var" / "backup"


def _refs_out(refs) -> list[dict]:
    return [
        {
            "assetId": ref.asset_id,
            "position": ref.position,
            "role": ref.role,
            "description": ref.description,
        }
        for ref in refs
    ]


def _draft_from(
    pack: TemplatePack,
    refs: list[dict],
    *,
    required_roles: list[str],
    cover_asset_id: str | None,
) -> PackDraftIn:
    """把「要写成什么样」翻译成草稿入参；引用图整组覆盖，不逐条 diff。"""
    return PackDraftIn(
        request_id=uuid.UUID(hex=pack.id),
        expected_revision=pack.revision,
        kind=pack.kind,  # type: ignore[arg-type]
        name=pack.name,
        reference_ids=[row["assetId"] for row in refs],
        # 主图被摘掉时才回落到剩下的第一张；正常情况下 cover 本来就是正面主图。
        cover_reference_id=(
            cover_asset_id
            if cover_asset_id in {row["assetId"] for row in refs}
            else refs[0]["assetId"]
        ),
        wardrobe_mode=pack.wardrobe_mode,  # type: ignore[arg-type]
        features=pack.features,
        instructions=pack.instructions,
        bindings=[
            PackBinding(role=row["role"], description=row["description"]) for row in refs
        ],
        # 必填用途必须跟着图一起收：留着 `detail` 会导致 `gaps()` 报「缺少产品细节图」，
        # 发布直接被拦下。基准用途 front 由 `aligned_required_roles` 兜底补回。
        required_roles=[role for role in required_roles if role not in DROP_ROLES],
        supported_scopes=list(pack.supported_scopes),
    )


async def plan(session, service: PackService, repo: PackRepository, only: set[str]):
    """算出每个包「摘什么、留什么」，不写库。"""
    stmt = select(TemplatePack).where(
        TemplatePack.kind == "product", TemplatePack.deleted_at.is_(None)
    )
    packs = list((await session.scalars(stmt)).all())
    if only:
        packs = [pack for pack in packs if pack.id in only]
    rows = []
    for pack in packs:
        refs = list(await repo.references(pack.id))
        dropped = [ref for ref in refs if ref.role in DROP_ROLES]
        kept = [ref for ref in refs if ref.role not in DROP_ROLES]
        present = {ref.role for ref in kept}
        rows.append(
            {
                "pack": pack,
                "before": _refs_out(refs),
                "dropped": _refs_out(dropped),
                "kept": _refs_out(kept),
                "beforeRequiredRoles": list(pack.required_roles),
                "afterRequiredRoles": [
                    role for role in pack.required_roles if role not in DROP_ROLES
                ],
                "skipped": not dropped,
                # 留下的用途必须能把模板要的两张凑齐，凑不齐就不动它：
                # 半成品包比带细节图的包更糟（运行时会直接报「缺少多视结构图」）。
                "missingKeptRoles": [
                    role for role in KEEP_ROLES if role not in present
                ],
            }
        )
    return rows


async def apply(session, service: PackService, rows: list[dict]) -> tuple[list[dict], Path | None]:
    snapshot = {
        "createdAt": datetime.now(UTC).isoformat(),
        "reason": "摘掉产品素材包的产品细节图，只保留正面主图与三视图",
        "packs": [],
    }
    for row in rows:
        if row["skipped"] or row["missingKeptRoles"]:
            continue
        pack = row["pack"]
        snapshot["packs"].append(
            {
                "packId": pack.id,
                "name": pack.name,
                "kind": pack.kind,
                "version": pack.version,
                "revision": pack.revision,
                "references": row["before"],
                "requiredRoles": row["beforeRequiredRoles"],
                "supportedScopes": list(pack.supported_scopes),
                "coverAssetId": pack.cover_asset_id,
            }
        )
    if not snapshot["packs"]:
        return [], None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = BACKUP_DIR / f"product-pack-detail-prune-{stamp}.json"
    # 独占创建：同名文件已存在时宁可失败，也不覆盖上一份快照。
    with path.open("x", encoding="utf-8") as stream:
        json.dump(snapshot, stream, ensure_ascii=False, indent=2)

    results = []
    for row in snapshot["packs"]:
        pack = next(item["pack"] for item in rows if item["pack"].id == row["packId"])
        kept = next(item["kept"] for item in rows if item["pack"].id == row["packId"])
        after_required = next(
            item["afterRequiredRoles"] for item in rows if item["pack"].id == row["packId"]
        )
        draft = _draft_from(
            pack,
            kept,
            required_roles=after_required,
            cover_asset_id=pack.cover_asset_id,
        )
        saved = await service.save(draft, pack.owner_id)
        published = await service.publish(pack.id, pack.owner_id, saved.revision)
        # 与 `pack_router.publish` 同一条留痕：脚本发布的版本同样要能在审计里查到。
        await audit.record(
            "template.pack.publish",
            actor_id=pack.owner_id,
            target_type="media_pack",
            target_id=pack.id,
            detail={
                "revision": saved.revision,
                "version": published.version,
                "status": published.status,
                "reason": "摘除产品细节图，只保留正面主图与三视图",
            },
        )
        results.append(
            {
                "packId": pack.id,
                "name": pack.name,
                "version": f"{row['version']} → {published.version}",
                "revision": published.revision,
                "requiredRoles": published.required_roles,
                "gaps": published.gaps,
            }
        )
    return results, path


async def rollback(session, service: PackService, snapshot_path: Path) -> list[dict]:
    """按快照重新发布一版：历史版本不可变，所以「恢复」是新增一版而不是改回去。"""
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    results = []
    for row in snapshot["packs"]:
        pack = await session.get(TemplatePack, row["packId"])
        if pack is None or pack.deleted_at is not None:
            results.append({"packId": row["packId"], "error": "素材包不存在"})
            continue
        draft = _draft_from(
            pack,
            row["references"],
            required_roles=row["requiredRoles"],
            cover_asset_id=row["coverAssetId"],
        )
        saved = await service.save(draft, pack.owner_id)
        published = await service.publish(pack.id, pack.owner_id, saved.revision)
        await audit.record(
            "template.pack.publish",
            actor_id=pack.owner_id,
            target_type="media_pack",
            target_id=pack.id,
            detail={
                "revision": saved.revision,
                "version": published.version,
                "status": published.status,
                "reason": f"回滚到细节图摘除前的状态（快照 {snapshot_path.name}）",
            },
        )
        results.append(
            {
                "packId": pack.id,
                "name": pack.name,
                "version": f"{row['version']} → {published.version}",
                "requiredRoles": published.required_roles,
            }
        )
    return results


async def run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="保存草稿并发布新版本")
    parser.add_argument(
        "--pack-id", action="append", default=[], help="只处理指定素材包，可重复"
    )
    parser.add_argument("--rollback", type=Path, help="用 --apply 生成的快照复原")
    args = parser.parse_args()

    async with get_session_factory()() as session:
        service = PackService(session)
        repo = PackRepository(session)
        if args.rollback:
            results = await rollback(session, service, args.rollback)
            print(json.dumps({"mode": "rollback", "results": results}, ensure_ascii=False, indent=2))
            return 0

        rows = await plan(session, service, repo, set(args.pack_id))
        summary = [
            {
                "packId": row["pack"].id,
                "name": row["pack"].name,
                "version": row["pack"].version,
                "beforeRequiredRoles": row["beforeRequiredRoles"],
                "afterRequiredRoles": row["afterRequiredRoles"],
                "drop": [
                    f'{item["role"]}:{item["description"][:20]}' for item in row["dropped"]
                ],
                "dropAssetIds": [item["assetId"] for item in row["dropped"]],
                "keep": [item["role"] for item in row["kept"]],
                "status": (
                    "无需处理"
                    if row["skipped"]
                    else (
                        "跳过：保留图凑不齐正面/三视图"
                        if row["missingKeptRoles"]
                        else ("已发布" if args.apply else "待执行")
                    )
                ),
            }
            for row in rows
        ]
        if not args.apply:
            print(
                json.dumps(
                    {"mode": "preflight", "packs": summary, "message": "只读预览，未写库"},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        results, path = await apply(session, service, rows)
        print(
            json.dumps(
                {
                    "mode": "apply",
                    "snapshot": str(path),
                    "before": summary,
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(run()))
