"""把归档盘上的老封装与老调用档案导入数据库（幂等）。

**输入**（全部只读）：

- `catalog/packs/<id>.json`        已发布封装的 head
- `catalog/pack_drafts/<id>.json`  草稿（与 head 同 id）
- `catalog/pack_versions/<id>/<v>.json` 不可变版本快照
- `catalog/runs/<id>.json`         指针，只含 `{"path": ...}`
- `<runs_root>/<path>/run.json`    调用档案本体

**输出**：六张表。主键沿用老 id，所以重跑安全。

**字段兼容**：老 JSON 有两种写法（`model_dump()` 出 snake_case、
`model_dump(by_alias=True)` 出 camelCase），历史上两种都写进过盘里。这里用
`_pick()` 两种都认，而不是猜一种——猜错的表现是「导入成功但字段全是默认值」，
比报错难查得多。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from infra.archive import ArchiveStorage
from infra.archive.runs import runs_root
from modules.template.models import (
    ReplacementReview,
    ReplacementRun,
    ReplacementRunInput,
    TemplatePack,
    TemplatePackReference,
    TemplatePackVersion,
)

PREFIXES = {
    "packs": "catalog/packs",
    "drafts": "catalog/pack_drafts",
    "versions": "catalog/pack_versions",
    "runs": "catalog/runs",
}


@dataclass
class ImportReport:
    packs: int = 0
    versions: int = 0
    runs: int = 0
    inputs: int = 0
    reviews: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        rows = [
            f"封装 {self.packs} 条、版本 {self.versions} 条、调用档案 {self.runs} 条、"
            f"输入 {self.inputs} 条、评价 {self.reviews} 条",
            f"跳过（已存在或数据不可用）{self.skipped} 条",
        ]
        rows.extend(f"  · {note}" for note in self.notes)
        return rows


def _pick(value: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in value and value[key] is not None:
            return value[key]
    return default


def _dt(value: Any) -> datetime | None:
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return None


async def import_legacy(
    session: AsyncSession, storage: ArchiveStorage, *, dry_run: bool = False
) -> ImportReport:
    report = ImportReport()
    await _import_packs(session, storage, report, dry_run=dry_run)
    await _import_runs(session, storage, report, dry_run=dry_run)
    if not dry_run:
        await session.commit()
    return report


# --------------------------------------------------------------------------- #
# 封装
# --------------------------------------------------------------------------- #
async def _import_packs(
    session: AsyncSession, storage: ArchiveStorage, report: ImportReport, *, dry_run: bool
) -> None:
    heads = {_pick(raw, "id"): raw for raw in _load_json(storage, PREFIXES["packs"], report)}
    drafts = {_pick(raw, "id"): raw for raw in _load_json(storage, PREFIXES["drafts"], report)}
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
        revision = int(_pick(draft, "revision", default=0) or 0) or 1
        pack = TemplatePack(
            id=pack_id,
            kind=str(_pick(source, "kind", default="character")),
            name=str(_pick(source, "name", default="")),
            owner_id=str(_pick(source, "ownerId", "owner_id", default="")),
            status=str(_pick(source, "status", default="draft")),
            version=int(_pick(source, "version", default=0) or 0),
            revision=revision,
            features=str(_pick(source, "features", default="") or ""),
            instructions=str(_pick(source, "instructions", default="") or ""),
            wardrobe_mode=str(_pick(source, "wardrobeMode", "wardrobe_mode", default="preserve")),
            cover_asset_id=_pick(source, "coverReferenceId", "cover_reference_id"),
            required_roles=list(_pick(source, "requiredRoles", "required_roles", default=[]) or []),
            supported_scopes=list(
                _pick(source, "supportedScopes", "supported_scopes", default=[]) or []
            ),
            published_at=_dt(_pick(source, "publishedAt", "published_at")),
        )
        if not dry_run:
            session.add(pack)
            await session.flush()
            await _write_references(session, pack.id, source, report)
        report.packs += 1

    for raw in _load_json(storage, PREFIXES["versions"], report):
        pack_id = str(_pick(raw, "id", default="") or "")
        version = int(_pick(raw, "version", default=0) or 0)
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
                        _pick(raw, "referenceIds", "reference_ids", default=[]) or []
                    ),
                    bindings=list(_pick(raw, "bindings", default=[]) or []),
                    required_roles=list(
                        _pick(raw, "requiredRoles", "required_roles", default=[]) or []
                    ),
                    supported_scopes=list(
                        _pick(raw, "supportedScopes", "supported_scopes", default=[]) or []
                    ),
                    features=str(_pick(raw, "features", default="") or ""),
                    instructions=str(_pick(raw, "instructions", default="") or ""),
                    wardrobe_mode=str(
                        _pick(raw, "wardrobeMode", "wardrobe_mode", default="preserve")
                    ),
                    published_at=_dt(_pick(raw, "publishedAt", "published_at")),
                )
            )
        report.versions += 1


async def _version_exists(session: AsyncSession, pack_id: str, version: int) -> bool:
    stmt = select(TemplatePackVersion.id).where(
        TemplatePackVersion.pack_id == pack_id, TemplatePackVersion.version == version
    )
    return await session.scalar(stmt) is not None


async def _write_references(
    session: AsyncSession, pack_id: str, source: dict[str, Any], report: ImportReport
) -> None:
    ids = list(_pick(source, "referenceIds", "reference_ids", default=[]) or [])
    bindings = list(_pick(source, "bindings", default=[]) or [])
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


# --------------------------------------------------------------------------- #
# 调用档案
# --------------------------------------------------------------------------- #
async def _import_runs(
    session: AsyncSession, storage: ArchiveStorage, report: ImportReport, *, dry_run: bool
) -> None:
    for pointer in _load_json(storage, PREFIXES["runs"], report):
        raw_path = str(pointer.get("path") or "") if isinstance(pointer, dict) else ""
        run_id = _pointer_id(pointer, raw_path)
        if not run_id:
            report.skipped += 1
            continue
        if await session.get(ReplacementRun, run_id) is not None:
            report.skipped += 1
            continue
        folder = _run_dir(raw_path, run_id)
        meta = _read_manifest(folder) if folder is not None else None
        if folder is None or meta is None:
            report.skipped += 1
            report.notes.append(f"档案 {run_id} 的工作区不在本机，已跳过（指针保留）")
            continue
        # dry-run 也走同一段读盘逻辑，只是最后不 add 进 session：报告里的
        # 「输入 N 条」必须是真实数字，否则运维会以为迁移漏了东西。
        await _write_run(session, run_id, folder, meta, report, dry_run=dry_run)
        report.runs += 1


async def _write_run(
    session: AsyncSession,
    run_id: str,
    folder: Path,
    meta: dict[str, Any],
    report: ImportReport,
    *,
    dry_run: bool = False,
) -> None:
    """一条档案的四个来源文件 → 三张表。

    老档案把「一条调用」拆在三个地方，全都要读，缺一个就会丢东西：

    | 文件 | 是什么 | 丢了会怎样 |
    |---|---|---|
    | `run.json` | 状态机快照 | 列表里状态全是错的 |
    | `inputs/prompt.txt` | 编译后的提示词 | 不知道当初到底让模型做什么 |
    | `inputs/input-map.json` | 逐条输入与 sha256 | 重跑无法复原输入 |
    | `review/review.json` | 用户评价 | 评价记录凭空消失 |
    """
    inputs = _read_inputs(folder)
    review = _read_json(folder / "review" / "review.json")
    summary = _pick(meta, "input_summary", "inputSummary")
    if not isinstance(summary, dict):
        # 老 run.json 没有 input_summary：用 input-map 拼出列表页要的那份摘要，
        # 否则新列表页拿不到原片名，只能显示「原片已不在」。
        summary = _summary_from(inputs, meta)

    run = ReplacementRun(
        id=run_id,
        kind=str(_pick(meta, "kind", default="character")),
        target=str(_pick(meta, "target", default="")),
        owner_id=str(_pick(meta, "owner_id", "ownerId", default="")),
        status=str(_pick(meta, "status", default="prepared")),
        framework_status=_pick(meta, "framework_status", "frameworkStatus"),
        fingerprint=str(_pick(meta, "fingerprint", default="")),
        prompt=_read_text(folder / "inputs" / "prompt.txt"),
        prompt_spec=_dict_or_none(_read_json(folder / "inputs" / "prompt-spec.json")),
        configuration=_dict_or_none(_read_json(folder / "inputs" / "configuration.json")),
        input_summary=summary,
        task_id=_pick(meta, "task_id", "taskId"),
        duration=int(_pick(meta, "duration", default=0) or 0),
        progress_percent=int(_pick(meta, "progress_percent", "progressPercent", default=0) or 0),
        estimated_credits=_pick(meta, "estimated_credits", "estimatedCredits"),
        credits=_pick(meta, "credits"),
        billing_mode=str(_pick(meta, "billing_mode", "billingMode", default="free")),
        submit_attempts=int(_pick(meta, "submit_attempts", "submitAttempts", default=0) or 0),
        submit_mode=_pick(meta, "submit_mode", "submitMode"),
        retry_count=int(_pick(meta, "retry_count", "retryCount", default=0) or 0),
        last_error=_pick(meta, "last_error", "lastError"),
        error_code=_pick(meta, "error_code", "errorCode"),
        deleted_at=_dt(_pick(meta, "deleted_at", "deletedAt")),
    )
    created = _dt(_pick(meta, "created_at", "createdAt"))
    if created is not None:
        run.created_at = created

    pending: list[Any] = [run]
    for position, item in enumerate(inputs):
        pending.append(
            ReplacementRunInput(
                run_id=run_id,
                asset_id=str(item.get("id") or ""),
                role=str(item.get("role") or ""),
                position=position,
                usage=str(item.get("usage") or ""),
                # 老档案这里是工作区内的相对路径（`inputs/reference-1.png`）。
                # 文件迁进对象存储时这一步会改写成桶里的 key，见 §6 的迁移命令。
                storage_key=str(item.get("file") or ""),
            )
        )
        report.inputs += 1

    status = str((review or {}).get("status") or _pick(meta, "review_status", "reviewStatus") or "")
    if status and status != "pending":
        pending.append(
            ReplacementReview(
                run_id=run_id,
                status=status,
                version=int(_pick(review or {}, "version", default=0) or 0),
                reason_codes=list(
                    _pick(review or {}, "reason_codes", "reasonCodes", default=[]) or []
                ),
                comment=str(_pick(review or {}, "comment", default="") or ""),
                reviewed_by=_pick(review or {}, "reviewed_by", "reviewedBy"),
                reviewed_at=_dt(_pick(review or {}, "reviewed_at", "reviewedAt")),
            )
        )
        report.reviews += 1

    if dry_run:
        return
    session.add_all(pending)
    await session.flush()


def _summary_from(inputs: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any] | None:
    """input-map → 列表页摘要（`ReplacementInputSummary` 的形状）。"""
    if not inputs:
        return None
    assets = [
        {
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or ""),
            "role": str(item.get("role") or ""),
            "type": str(item.get("type") or "image"),
            # 与素材模块的出参同一个拼法：`{api_prefix}/assets/{id}/file`。
            # 不 import 素材模块（业务模块之间不许互相 import，见 EXTENDING §5.1）。
            "url": f"{settings.api_prefix}/assets/{item.get('id')}/file",
        }
        for item in inputs
        if item.get("id")
    ]
    if not assets:
        return None
    return {
        "assets": assets,
        # 封面固定是第 1 张参考图：老 prepare 的约定，换了会让历史记录显示另一张图。
        "cover_reference_id": next(
            (item["id"] for item in assets if item["role"] != "source_video"), None
        ),
        "wardrobe_mode": str(_pick(meta, "wardrobe_mode", "wardrobeMode", default="preserve")),
        "replacement_scope": "",
        "notes": "",
    }


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


# --------------------------------------------------------------------------- #
# 读盘
# --------------------------------------------------------------------------- #
def _load_json(storage: ArchiveStorage, prefix: str, report: ImportReport) -> list[dict]:
    """枚举前缀下所有 JSON。单个文件坏掉只跳过它，不中断整次导入。"""
    rows: list[dict] = []
    for key in storage.list_keys(prefix, suffix=".json"):
        try:
            value = storage.read(key)
        except (OSError, ValueError) as exc:
            report.notes.append(f"读取 {key} 失败：{exc}")
            continue
        if isinstance(value, dict):
            rows.append(value)
        elif isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    return rows


def _pointer_id(pointer: object, raw_path: str) -> str:
    if isinstance(pointer, dict):
        value = pointer.get("id")
        if isinstance(value, str) and value:
            return value
    tail = raw_path.rstrip("/").rsplit("/", 1)[-1]
    # 老目录名形如 `product-<32hex>`，把前缀去掉。
    candidate = tail.split("-")[-1]
    return candidate if len(candidate) == 32 else ""


def _run_dir(raw_path: str, run_id: str) -> Path | None:
    """指针 → 工作区目录；不在本机就返回 None。

    「指针在、工作区不在」不是数据损坏，换台机器的必然表现。这里返回 None 让
    调用方记一条 note 后跳过，而不是让整次导入失败。
    """
    try:
        root = runs_root()
    except Exception:  # noqa: BLE001 - 归档根没配时工作区无法确定，按「不在本机」处理
        return None
    # 老指针存「相对归档根」（`runs/2026/...`），新指针存「相对工作区根」，两种都认。
    relative = raw_path[len("runs/") :] if raw_path.startswith("runs/") else raw_path
    if relative:
        candidate = (root / relative).resolve()
        if (candidate / "run.json").is_file():
            return candidate
    if not run_id:
        return None
    # 指针坏了但目录还在：按 `<年>/<月>/<日>/<kind>-<id>` 的布局找回一次。
    for hit in sorted(root.glob(f"*/*/*/*-{run_id}")):
        if (hit / "run.json").is_file():
            return hit
    return None


def _read_manifest(folder: Path) -> dict[str, Any] | None:
    """工作区里的 `run.json` —— 老调用档案的状态机快照。"""
    value = _read_json(folder / "run.json")
    return value if isinstance(value, dict) else None


def _read_inputs(folder: Path) -> list[dict[str, Any]]:
    """`inputs/input-map.json` —— 这次实际用了哪几张图、什么用途、sha256。

    老档案里逐条输入只存在这个文件里（`run.json` 没有它），读不到就等于
    「重跑无法复原输入」，而重跑正是替换功能最常用的动作。
    """
    value = _read_json(folder / "inputs" / "input-map.json")
    if isinstance(value, dict):
        value = _pick(value, "items", "assets", "inputs")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return ""
