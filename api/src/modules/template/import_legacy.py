"""把归档盘上的老调用档案（指针 + `run.json` + `input-map.json`）导入数据库（幂等）。

**为什么还留着**：档案从「`runs/<id>/run.json`」搬到数据库之后，存量不会自己
跟过来；而档案是「重跑与复核」的唯一依据。

**为什么与封装分成两条命令**：调用档案这张表归本模块，素材包归
`modules/template_pack`，业务模块之间不许互相 import。运维按顺序跑两条：

    app import-template-packs && app import-template-runs

三条设计约束与封装那半一致：幂等（主键沿用老 id）、不删老文件、缺字段按默认
值补并在报告里说明。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.exceptions import Unavailable
from core.legacy_json import as_datetime, dict_or_none, load_json, pick, read_json, read_text
from infra.archive import ArchiveStorage
from infra.archive.runs import runs_root
from modules.template.models import (
    ReplacementReview,
    ReplacementRun,
    ReplacementRunInput,
)

PREFIXES = {
    "runs": "catalog/runs",
}


@dataclass
class RunImportReport:
    runs: int = 0
    inputs: int = 0
    reviews: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        rows = [
            f"调用档案 {self.runs} 条、输入 {self.inputs} 条、评价 {self.reviews} 条",
            f"跳过（已存在或数据不可用）{self.skipped} 条",
        ]
        rows.extend(f"  · {note}" for note in self.notes)
        return rows


async def import_legacy_runs(
    session: AsyncSession, storage: ArchiveStorage, *, dry_run: bool = False
) -> RunImportReport:
    # 归档根不可用是**部署故障**，不是「这条档案不在本机」。以前 `_run_dir` 把它
    # 吞成 `None`，整次导入会显示「0 条导入、N 条跳过」并以 0 退出 —— 运维看到
    # 的是「没有存量数据」，而真相是配置少了一项。这里前置探测，直接失败。
    try:
        runs_root()
    except Exception as exc:  # noqa: BLE001 - 任何原因都意味着工作区不可确定
        raise Unavailable(
            "运行工作区根不可用，无法导入调用档案："
            "检查 REPLACEMENT_RUNS_ROOT 或 ARCHIVE_DRIVER 配置"
        ) from exc
    report = RunImportReport()
    await _import_runs(session, storage, report, dry_run=dry_run)
    if not dry_run:
        await session.commit()
    return report


async def _import_runs(
    session: AsyncSession, storage: ArchiveStorage, report: RunImportReport, *, dry_run: bool
) -> None:
    for pointer in load_json(storage, PREFIXES["runs"], report.notes):
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
    report: RunImportReport,
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
    review = read_json(folder / "review" / "review.json")
    summary = pick(meta, "input_summary", "inputSummary")
    if not isinstance(summary, dict):
        # 老 run.json 没有 input_summary：用 input-map 拼出列表页要的那份摘要，
        # 否则新列表页拿不到原片名，只能显示「原片已不在」。
        summary = _summary_from(inputs, meta)

    run = ReplacementRun(
        id=run_id,
        kind=str(pick(meta, "kind", default="character")),
        target=str(pick(meta, "target", default="")),
        owner_id=str(pick(meta, "owner_id", "ownerId", default="")),
        status=str(pick(meta, "status", default="prepared")),
        framework_status=pick(meta, "framework_status", "frameworkStatus"),
        fingerprint=str(pick(meta, "fingerprint", default="")),
        prompt=read_text(folder / "inputs" / "prompt.txt"),
        prompt_spec=dict_or_none(read_json(folder / "inputs" / "prompt-spec.json")),
        configuration=dict_or_none(read_json(folder / "inputs" / "configuration.json")),
        input_summary=summary,
        task_id=pick(meta, "task_id", "taskId"),
        duration=int(pick(meta, "duration", default=0) or 0),
        progress_percent=int(pick(meta, "progress_percent", "progressPercent", default=0) or 0),
        estimated_credits=pick(meta, "estimated_credits", "estimatedCredits"),
        credits=pick(meta, "credits"),
        billing_mode=str(pick(meta, "billing_mode", "billingMode", default="free")),
        submit_attempts=int(pick(meta, "submit_attempts", "submitAttempts", default=0) or 0),
        submit_mode=pick(meta, "submit_mode", "submitMode"),
        retry_count=int(pick(meta, "retry_count", "retryCount", default=0) or 0),
        last_error=pick(meta, "last_error", "lastError"),
        error_code=pick(meta, "error_code", "errorCode"),
        deleted_at=as_datetime(pick(meta, "deleted_at", "deletedAt")),
    )
    created = as_datetime(pick(meta, "created_at", "createdAt"))
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

    status = str((review or {}).get("status") or pick(meta, "review_status", "reviewStatus") or "")
    if status and status != "pending":
        pending.append(
            ReplacementReview(
                run_id=run_id,
                status=status,
                version=int(pick(review or {}, "version", default=0) or 0),
                reason_codes=list(
                    pick(review or {}, "reason_codes", "reasonCodes", default=[]) or []
                ),
                comment=str(pick(review or {}, "comment", default="") or ""),
                reviewed_by=pick(review or {}, "reviewed_by", "reviewedBy"),
                reviewed_at=as_datetime(pick(review or {}, "reviewed_at", "reviewedAt")),
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
        "wardrobe_mode": str(pick(meta, "wardrobe_mode", "wardrobeMode", default="preserve")),
        "replacement_scope": "",
        "notes": "",
    }


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

    `runs_root()` 的失败**不在这里处理**：它是部署配置问题，由 `import_legacy()`
    前置校验成整次失败。两件事以前混在一起，结果是「配置少一项」被报成
    「这批档案都不在这台机器上」。
    """
    root = runs_root().resolve()
    # 老指针存「相对归档根」（`runs/2026/...`），新指针存「相对工作区根」，两种都认。
    relative = raw_path[len("runs/") :] if raw_path.startswith("runs/") else raw_path
    if relative:
        candidate = (root / relative).resolve()
        # 指针来自老 JSON，绝对路径或 `..` 都能让它跳出归档根。导入本身只读，
        # 但把工作区之外的文件当档案读进来仍是越界，一条判断就能拦住。
        if candidate.is_relative_to(root) and (candidate / "run.json").is_file():
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
    value = read_json(folder / "run.json")
    return value if isinstance(value, dict) else None


def _read_inputs(folder: Path) -> list[dict[str, Any]]:
    """`inputs/input-map.json` —— 这次实际用了哪几张图、什么用途、sha256。

    老档案里逐条输入只存在这个文件里（`run.json` 没有它），读不到就等于
    「重跑无法复原输入」，而重跑正是替换功能最常用的动作。
    """
    value = read_json(folder / "inputs" / "input-map.json")
    if isinstance(value, dict):
        value = pick(value, "items", "assets", "inputs")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


