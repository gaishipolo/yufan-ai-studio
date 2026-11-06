"""只读旧库，复制已登记原片和已发布素材包；不移动历史资料，不触发生成。

素材登记自「索引搬进 `assets` 表」后是异步 + 需会话的，所以这里每次登记
`asyncio.run` 一次：一次性迁移脚本，几百个文件多开几个连接无所谓，换来的好处是
复制、缩略图、DB 行、JSON 镜像全走与线上同一条代码路径，不另写一份实现。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
# 同目录下的封装同步脚本也要能直接 import
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pydantic import TypeAdapter  # noqa: E402

from core.media import MediaCategory, StoredMedia  # noqa: E402
from db.session import get_session_factory  # noqa: E402
from infra.archive import BlobStore, get_archive_storage  # noqa: E402
from infra.archive.base import checksum, within  # noqa: E402
from modules.asset.archive_service import ArchiveAssetService  # noqa: E402

# 目标端必须走驱动工厂：旧库在本地盘上读（`--legacy`），但**写**到哪由
# `ARCHIVE_DRIVER` 决定。直接 `LocalArchiveStorage()` 会在切 S3 后把几百条
# 记录和缩略图写回本地旧目录，脚本自报成功而线上一条都看不到。
storage = get_archive_storage()
blobs = BlobStore(storage)


def read_mapping(key: str) -> dict:
    value = storage.read(key)
    if not isinstance(value, dict):
        raise ValueError("迁移索引不是对象")
    return value


def registered(source: Path, name: str, category: str, owner: str) -> dict:
    parsed = TypeAdapter(MediaCategory).validate_python(category)

    async def run() -> dict:
        async with get_session_factory()() as session:
            value = await ArchiveAssetService(session).register(source, name, parsed, owner)
            return value.model_dump(mode="json", by_alias=True)

    return asyncio.run(run())


def asset_dict(asset_id: str, owner: str) -> dict:
    """已登记过的素材从 JSON 镜像读回（迁移脚本只认存储里那份）。"""
    key = f"catalog/assets/{asset_id}.json"
    if not storage.exists(key):
        raise FileNotFoundError(f"索引缺失：{key}（请先跑 app import-archive-index）")
    media = StoredMedia.model_validate(storage.read(key))
    if media.owner_id != owner:
        raise PermissionError(f"素材 {asset_id} 不属于 {owner}")
    return media.model_dump(mode="json", by_alias=True)


def ensure_preview(value: dict) -> None:
    media = StoredMedia.model_validate(value)
    if media.preview_path and storage.exists(media.preview_path):
        return
    key = f"library/previews/{media.id}.jpg"
    # 派生要真实路径：BlobStore 负责物化原片、在临时目录出图、再回传目标 key，
    # 本地与远端驱动走同一条路，脚本不再自己拼 `resolve()` 出来的本地路径。
    if blobs.derive(media.path, key, kind=media.type):
        media.preview_path = key
        media.preview_url = f"/api/v1/assets/archive/{media.id}/thumbnail"
        storage.write(
            f"catalog/assets/{media.id}.json",
            media.model_dump(mode="json", by_alias=True),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy", default="E:/yufan-ai")
    parser.add_argument("--owner", default="u_001")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    legacy = Path(args.legacy).resolve()
    connection = sqlite3.connect((legacy / "db.sqlite3").as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    materials = {
        row["id"]: dict(row) for row in connection.execute("SELECT * FROM libtv_workbench_material")
    }
    published = [
        dict(row)
        for row in connection.execute(
            "SELECT p.id,p.name,p.kind,p.published_revision_id,"
            "r.features,r.instructions,r.wardrobe_mode,"
            "r.supported_scopes,r.required_roles,r.version "
            "FROM libtv_workbench_referencepack p JOIN libtv_workbench_packrevision r "
            "ON p.published_revision_id=r.id WHERE r.status='published'"
        )
    ]
    selected = [(value, "source") for value in materials.values() if value["kind"] == "video"]
    for pack in published:
        pack["bindings"] = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM libtv_workbench_packimagebinding "
                "WHERE revision_id=? ORDER BY position",
                (pack["published_revision_id"],),
            )
        ]
        selected.extend((materials[b["material_id"]], pack["kind"]) for b in pack["bindings"])
    unique = {(value["id"], category): value for value, category in selected}
    summary = {
        "files": len(unique),
        "bytes": sum(v["size"] for v in unique.values()),
        "publishedPacks": len(published),
        "execute": args.execute,
    }
    print(json.dumps(summary))
    if not args.execute:
        return
    index = "catalog/legacy-import.json"
    imported = read_mapping(index) if storage.exists(index) else {"assets": {}, "packs": {}}
    for (old_id, category), value in unique.items():
        key = f"{args.owner}:{old_id}:{category}"
        if key in imported["assets"]:
            asset = asset_dict(imported["assets"][key]["id"], args.owner)
            ensure_preview(asset)
            storage.write(f"catalog/assets/{asset['id']}.json", asset)
            continue
        source = within(legacy, value["path"])
        if checksum(source) != value["sha256"]:
            raise RuntimeError(f"旧素材哈希变化，停止复制: {old_id}")
        asset = registered(source, value["name"], category, args.owner)
        imported["assets"][key] = {
            "id": asset["id"],
            "oldId": old_id,
            "sha256": asset["sha256"],
            "path": asset["path"],
        }
        storage.write(index, imported)
    # 封装记录交给 sync_legacy_packs：那里经 PackService 保存并发布，
    # 会补齐逐图用途、必备用途和替换范围。这里若直接写 JSON，
    # 缺 status/version 的包会被读成草稿，模板页永远选不到。
    from sync_legacy_packs import run_sync

    for line in run_sync(legacy, args.owner, execute=True):
        print(line)
    for pack in published:
        imported["packs"][pack["id"]] = {
            "name": pack["name"],
            "referenceIds": [
                imported["assets"][f"{args.owner}:{b['material_id']}:{pack['kind']}"]["id"]
                for b in pack["bindings"]
            ],
        }
    imported["completedAt"] = datetime.now(UTC).isoformat()
    storage.write(index, imported)
    print("Copied and verified; no generation submitted.")


if __name__ == "__main__":
    main()
