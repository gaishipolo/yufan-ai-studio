"""把旧工作台已发布的封装同步成新平台的完整封装（可重复执行）。

与 `import_legacy_replacement_assets.py` 的分工：
- 那个脚本只负责把旧素材按字节复制进新目录并登记媒体；
- 本脚本负责"封装"这一层：逐图用途、必备用途、产品替换范围、发布版本。

所有写入都走 `PackService.save` + `PackService.publish`，不手写 `catalog/packs/*.json`。
手写 JSON 正是上一轮的问题根源：旧字段名对不上新模型，Pydantic 用默认值补成
`status=draft, version=0`，包看起来存在，模板页却永远读不到。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.exceptions import AppError, NotFound  # noqa: E402
from core.media import MediaPack  # noqa: E402
from infra.media_catalog import MediaCatalog  # noqa: E402
from modules.template.pack_service import PackService  # noqa: E402
from modules.template.replacement_schema import PackDraftIn  # noqa: E402


@dataclass
class LegacyPack:
    id: str
    kind: str
    name: str
    variant: str
    version: int
    revision_id: str
    wardrobe_mode: str
    features: str
    instructions: str
    required_roles: list[str]
    supported_scopes: list[str]
    bindings: list[dict[str, str]] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        """旧库把颜色/发型放在 variant 里，新契约没有这一列，折进名称避免选错款。"""
        name = self.name.strip()
        variant = (self.variant or "").strip()
        return name if not variant or variant in name else f"{name} {variant}"


def resolve_references(
    pack: LegacyPack,
    materials: dict[str, dict],
    binding_rows: list[dict],
    locator: AssetLocator,
    owner: str,
) -> list[str]:
    refs: list[str] = []
    for row in binding_rows:
        material = materials.get(row["material_id"])
        if not material:
            raise SystemExit(f"{pack.name}：旧库缺少素材 {row['material_id']}")
        refs.append(locator.find(owner, row["material_id"], pack.kind, material["sha256"]))
    return refs


class AssetLocator:
    """旧素材 id → 新平台媒体 id；先查迁移索引，再按 sha256 兜底。"""

    def __init__(self, catalog: MediaCatalog) -> None:
        self.catalog = catalog
        index_path = catalog.storage.root() / "catalog/legacy-import.json"
        index = catalog.storage.read(index_path) if index_path.is_file() else {}
        self.index: dict[str, dict] = (index or {}).get("assets", {})

    def find(self, owner: str, material_id: str, kind: str, sha256: str) -> str:
        entry = self.index.get(f"{owner}:{material_id}:{kind}")
        if entry:
            media = self.catalog.media(entry["id"])
            if media.category == kind and media.sha256 == sha256:
                return media.id
            raise SystemExit(f"迁移索引与素材不一致：{entry['id']}（{kind}）")
        for path in (self.catalog.storage.root() / "catalog/assets").glob("*.json"):
            value = self.catalog.storage.read(path)
            if (
                value.get("sha256") == sha256
                and value.get("category") == kind
                and value.get("ownerId") == owner
                and not value.get("deletedAt")
            ):
                return value["id"]
        raise SystemExit(f"新目录里没有对应素材：{material_id}（{kind}）")


def build_payload(pack: LegacyPack, refs: list[str], expected_revision: int) -> PackDraftIn:
    cover = next(
        (
            ref
            for ref, binding in zip(refs, pack.bindings, strict=True)
            if binding["role"] == "front"
        ),
        refs[0],
    )
    return PackDraftIn(
        request_id=UUID(pack.id),
        expected_revision=expected_revision,
        kind=pack.kind,
        name=pack.display_name,
        reference_ids=refs,
        cover_reference_id=cover,
        wardrobe_mode=pack.wardrobe_mode,
        features=pack.features,
        instructions=pack.instructions,
        bindings=[
            {"role": binding["role"], "description": binding["description"]}
            for binding in pack.bindings
        ],
        required_roles=pack.required_roles,
        supported_scopes=pack.supported_scopes,
    )


def unchanged(head: MediaPack, payload: PackDraftIn, gaps: list[str]) -> bool:
    return (
        head.status == "published"
        and head.kind == payload.kind
        and head.name == payload.name
        and head.reference_ids == payload.reference_ids
        and [b.role for b in head.bindings] == [b.role for b in payload.bindings]
        and [b.description for b in head.bindings] == [b.description for b in payload.bindings]
        and sorted(head.supported_scopes) == sorted(payload.supported_scopes)
        and sorted(head.required_roles) == sorted(payload.required_roles)
        and not gaps
    )


def run_sync(legacy: Path, owner: str, execute: bool) -> list[str]:
    packs, materials, bindings_by_pack = load_legacy(legacy)
    service = PackService()
    locator = AssetLocator(service.catalog)
    lines: list[str] = []
    for pack in packs:
        rows = bindings_by_pack[pack.id]
        refs = resolve_references(pack, materials, rows, locator, owner)
        try:
            current = service.draft(pack.id, owner)
            expected = current.revision
        except (NotFound, AppError):
            expected = 0
        payload = build_payload(pack, refs, expected)
        head = (
            service.catalog.pack(pack.id)
            if (service.storage.root() / "catalog/packs" / f"{pack.id}.json").is_file()
            else None
        )
        planned_gaps = service.gaps(
            MediaPack(
                id=pack.id,
                owner_id=owner,
                **payload.model_dump(exclude={"request_id", "expected_revision"}),
            )
        )
        if head and unchanged(head, payload, planned_gaps):
            lines.append(f"跳过 {payload.name}（已与旧库一致，v{head.version}）")
            continue
        if not execute:
            lines.append(
                f"待同步 {payload.name}：{len(refs)} 张 / 用途 {[b.role for b in payload.bindings]}"
                f" / 范围 {payload.supported_scopes} / 缺项 {planned_gaps or '无'}"
            )
            continue
        saved = service.save(payload, owner)
        published = service.publish(pack.id, owner, saved.revision)
        lines.append(
            f"已发布 {published.name}：v{published.version} / {len(published.reference_ids)} 张"
            f" / 用途 {[b.role for b in published.bindings]}"
            f" / 范围 {published.supported_scopes} / 缺项 {published.gaps or '无'}"
        )
    return lines


def load_legacy(legacy: Path) -> tuple[list[LegacyPack], dict[str, dict], dict[str, list[dict]]]:
    connection = sqlite3.connect((legacy / "db.sqlite3").as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT p.id,p.name,p.variant,p.kind,p.published_revision_id,"
        "r.version,r.wardrobe_mode,r.features,r.instructions,"
        "r.supported_scopes,r.required_roles "
        "FROM libtv_workbench_referencepack p "
        "JOIN libtv_workbench_packrevision r ON p.published_revision_id=r.id "
        "WHERE r.status='published' ORDER BY p.kind,p.name"
    ).fetchall()
    packs: list[LegacyPack] = []
    bindings_by_pack: dict[str, list[dict]] = {}
    for row in rows:
        bindings_by_pack[row["id"]] = [
            dict(item)
            for item in connection.execute(
                "SELECT material_id,role,description FROM libtv_workbench_packimagebinding "
                "WHERE revision_id=? ORDER BY position",
                (row["published_revision_id"],),
            )
        ]
        packs.append(
            LegacyPack(
                id=row["id"],
                kind=row["kind"],
                name=row["name"],
                variant=row["variant"],
                version=row["version"],
                revision_id=row["published_revision_id"],
                wardrobe_mode=row["wardrobe_mode"],
                features=row["features"] or "",
                instructions=row["instructions"] or "",
                required_roles=json.loads(row["required_roles"] or "[]"),
                supported_scopes=json.loads(row["supported_scopes"] or "[]"),
                bindings=bindings_by_pack[row["id"]],
            )
        )
    material_ids = {row["material_id"] for rows in bindings_by_pack.values() for row in rows}
    placeholders = ",".join("?" * len(material_ids))
    materials = {
        row["id"]: dict(row)
        for row in (
            connection.execute(
                "SELECT id,name,path,sha256 FROM libtv_workbench_material "
                f"WHERE id IN ({placeholders})",
                tuple(material_ids),
            )
            if material_ids
            else []
        )
    }
    connection.close()
    return packs, materials, bindings_by_pack


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy", default="E:/yufan-ai")
    parser.add_argument("--owner", default="u_001")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    for line in run_sync(Path(args.legacy).resolve(), args.owner, args.execute):
        print(line)


if __name__ == "__main__":
    main()
