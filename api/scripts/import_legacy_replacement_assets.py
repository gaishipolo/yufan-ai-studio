"""只读旧库，复制已登记原片和已发布素材包；不移动历史资料，不触发生成。"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pydantic import TypeAdapter  # noqa: E402

from core.media import MediaCategory, StoredMedia  # noqa: E402
from infra.storage.archive import LocalArchiveStorage, checksum, within  # noqa: E402
from modules.asset.archive_repository import ArchiveAssetRepository  # noqa: E402
from modules.asset.archive_service import ArchiveAssetService  # noqa: E402

storage = LocalArchiveStorage()
repo = ArchiveAssetRepository(storage)
service = ArchiveAssetService(storage)


def read_mapping(path: Path) -> dict:
    value = storage.read(path)
    if not isinstance(value, dict):
        raise ValueError("迁移索引不是对象")
    return value


def registered(source: Path, name: str, category: str, owner: str) -> dict:
    return repo.register(
        source, name, TypeAdapter(MediaCategory).validate_python(category), owner
    ).model_dump(mode="json", by_alias=True)


def asset_dict(asset_id: str, owner: str) -> dict:
    return service.owned(asset_id, owner).model_dump(mode="json", by_alias=True)


def ensure_preview(value: dict) -> None:
    media = StoredMedia.model_validate(value)
    repo.ensure_preview(media)
    value.update(media.model_dump(mode="json", by_alias=True))


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
    index = storage.root() / "catalog/legacy-import.json"
    imported = read_mapping(index) if index.exists() else {"assets": {}, "packs": {}}
    for (old_id, category), value in unique.items():
        key = f"{args.owner}:{old_id}:{category}"
        if key in imported["assets"]:
            asset = asset_dict(imported["assets"][key]["id"], args.owner)
            ensure_preview(asset)
            storage.write(storage.root() / "catalog/assets" / (asset["id"] + ".json"), asset)
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
    for pack in published:
        refs = [
            imported["assets"][f"{args.owner}:{b['material_id']}:{pack['kind']}"]["id"]
            for b in pack["bindings"]
        ]
        value = {
            "id": pack["id"],
            "name": pack["name"],
            "kind": pack["kind"],
            "referenceIds": refs,
            "wardrobeMode": pack["wardrobe_mode"],
            "features": pack["features"],
            "instructions": pack["instructions"],
            "ownerId": args.owner,
            "legacyRevisionId": pack["published_revision_id"],
            "bindings": pack["bindings"],
            "supportedScopes": json.loads(pack["supported_scopes"]),
            "requiredRoles": json.loads(pack["required_roles"]),
            "legacyVersion": pack["version"],
        }
        storage.write(storage.root() / "catalog/packs" / (pack["id"] + ".json"), value)
        imported["packs"][pack["id"]] = {"name": pack["name"], "referenceIds": refs}
    imported["completedAt"] = datetime.now(UTC).isoformat()
    storage.write(index, imported)
    print("Copied and verified; no generation submitted.")


if __name__ == "__main__":
    main()
