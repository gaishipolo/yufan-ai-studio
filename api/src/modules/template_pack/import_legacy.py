"""把归档盘上的老封装（head / 草稿 / 冻结版本）导入 `template_pack` 的三张表（幂等）。

**为什么还留着**：数据从「`catalog/*.json`」搬到数据库之后，存量数据不会自己
跟过来。没有这一步，从 JSON 时代升级上来等于「封装全清空」。

**为什么与调用档案分成两条命令**：一张表归一个模块（素材包/版本在本模块，
调用档案在 `modules/template`），而业务模块之间不许互相 import。合成一条
命令就得让某一半去写别人的表。运维按顺序跑两条即可：

    app import-template-packs && app import-template-runs

三条设计约束（与档案那半一致）：

1. **幂等**：主键沿用老 JSON 里的 id，第二次跑只跳过、不复制；可先 `--dry-run`。
2. **不删老文件**：JSON 留在归档盘上是回滚依据。
3. **不猜**：缺字段按默认值补，并在报告里说清补了什么。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.legacy_json import as_datetime, load_json, pick
from infra.archive import ArchiveStorage
from modules.template_pack.models import (
    TemplatePack,
    TemplatePackReference,
    TemplatePackVersion,
)

PREFIXES = {
    "packs": "catalog/packs",
    "drafts": "catalog/pack_drafts",
    "versions": "catalog/pack_versions",
}


@dataclass
class PackImportReport:
    packs: int = 0
    versions: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        rows = [
            f"封装 {self.packs} 条、版本 {self.versions} 条",
            f"跳过（已存在或数据不可用）{self.skipped} 条",
        ]
        rows.extend(f"  · {note}" for note in self.notes)
        return rows


async def import_legacy_packs(
    session: AsyncSession, storage: ArchiveStorage, *, dry_run: bool = False
) -> PackImportReport:
    report = PackImportReport()
    await _import_packs(session, storage, report, dry_run=dry_run)
    if not dry_run:
        await session.commit()
    return report


async def _import_packs(
    session: AsyncSession, storage: ArchiveStorage, report: PackImportReport, *, dry_run: bool
) -> None:
    heads = {pick(raw, "id"): raw for raw in load_json(storage, PREFIXES["packs"], report.notes)}
    drafts = {pick(raw, "id"): raw for raw in load_json(storage, PREFIXES["drafts"], report.notes)}
    # 只存在于 `pack_drafts/` 的是「存过但从没发布」的封装。老实现里它们从没进过
    # `packs/`，但用户看得见、编辑得到。只遍历 heads 会把这些草稿整批丢掉，
    # 表现是「升级后草稿箱空了」——所以取两份文件的并集。
    pack_ids = [key for key in (*heads, *drafts) if isinstance(key, str) and key]
    seen: set[str] = set()
    for pack_id in pack_ids:
        if pack_id in seen:
            continue
        seen.add(pack_id)
        existing = await session.get(TemplatePack, pack_id)
        if existing is not None:
            report.skipped += 1
            continue
        raw = heads.get(pack_id) or {}
        draft = drafts.get(pack_id) or {}
        # head 与草稿同 id：草稿是「正在编辑的那份」，head 是「已发布的那份」。
        # 内容取草稿（最后一次落盘），但**发布状态只能来自 head**——草稿里没有
        # 发布信息，用它覆盖会把已上架的封装降级成草稿。
        source = {**raw, **draft} if draft else raw
        if raw:
            for key in ("status", "version", "publishedAt", "published_at"):
                if key in raw:
                    source[key] = raw[key]
        revision = int(pick(draft, "revision", default=0) or 0) or 1
        pack = TemplatePack(
            id=pack_id,
            kind=str(pick(source, "kind", default="character")),
            name=str(pick(source, "name", default="")),
            owner_id=str(pick(source, "ownerId", "owner_id", default="")),
            status=str(pick(source, "status", default="draft")),
            version=int(pick(source, "version", default=0) or 0),
            revision=revision,
            features=str(pick(source, "features", default="") or ""),
            instructions=str(pick(source, "instructions", default="") or ""),
            wardrobe_mode=str(pick(source, "wardrobeMode", "wardrobe_mode", default="preserve")),
            cover_asset_id=pick(source, "coverReferenceId", "cover_reference_id"),
            required_roles=list(pick(source, "requiredRoles", "required_roles", default=[]) or []),
            supported_scopes=list(
                pick(source, "supportedScopes", "supported_scopes", default=[]) or []
            ),
            published_at=as_datetime(pick(source, "publishedAt", "published_at")),
        )
        if not dry_run:
            session.add(pack)
            await session.flush()
            await _write_references(session, pack.id, source, report)
        report.packs += 1

    for raw in load_json(storage, PREFIXES["versions"], report.notes):
        pack_id = str(pick(raw, "id", default="") or "")
        version = int(pick(raw, "version", default=0) or 0)
        if not pack_id or version < 1:
            report.skipped += 1
            continue
        if await _version_exists(session, pack_id, version):
            report.skipped += 1
            continue
        if not dry_run:
            session.add(
                TemplatePackVersion(
                    pack_id=pack_id,
                    version=version,
                    reference_ids=list(
                        pick(raw, "referenceIds", "reference_ids", default=[]) or []
                    ),
                    bindings=list(pick(raw, "bindings", default=[]) or []),
                    required_roles=list(
                        pick(raw, "requiredRoles", "required_roles", default=[]) or []
                    ),
                    supported_scopes=list(
                        pick(raw, "supportedScopes", "supported_scopes", default=[]) or []
                    ),
                    features=str(pick(raw, "features", default="") or ""),
                    instructions=str(pick(raw, "instructions", default="") or ""),
                    wardrobe_mode=str(
                        pick(raw, "wardrobeMode", "wardrobe_mode", default="preserve")
                    ),
                    published_at=as_datetime(pick(raw, "publishedAt", "published_at")),
                )
            )
        report.versions += 1


async def _version_exists(session: AsyncSession, pack_id: str, version: int) -> bool:
    stmt = select(TemplatePackVersion.id).where(
        TemplatePackVersion.pack_id == pack_id, TemplatePackVersion.version == version
    )
    return await session.scalar(stmt) is not None


async def _write_references(
    session: AsyncSession, pack_id: str, source: dict[str, Any], report: PackImportReport
) -> None:
    ids = list(pick(source, "referenceIds", "reference_ids", default=[]) or [])
    bindings = list(pick(source, "bindings", default=[]) or [])
    for position, asset_id in enumerate(ids):
        binding = bindings[position] if position < len(bindings) else {}
        if not isinstance(binding, dict):
            binding = {}
        session.add(
            TemplatePackReference(
                pack_id=pack_id,
                asset_id=str(asset_id),
                position=position,
                role=str(binding.get("role") or ""),
                description=str(binding.get("description") or ""),
            )
        )
    if not ids:
        report.notes.append(f"封装 {pack_id} 没有引用素材，已按空包导入")


