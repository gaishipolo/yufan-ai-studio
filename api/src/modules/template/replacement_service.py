"""替换模板业务编排；归档先登记后准备，失败可安全续做。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from core.exceptions import BadRequest, Conflict, Forbidden, NotFound, UpstreamError
from core.logging import get_logger
from core.media import StoredMedia
from infra.ai.libtv import get_libtv_settings
from infra.archive import checksum, lease
from modules.template.replacement_repository import ReplacementManifest, ReplacementRepository
from modules.template.replacement_schema import (
    PrepareReplacement,
    ReplacementCatalog,
    ReplacementPack,
    ReplacementRunOut,
)
from modules.template.replacement_worker import schedule

RUNTIME = Path(__file__).parent / "replacement_runtime"
BRIDGE = Path(__file__).parents[2] / "infra/ai/libtv_runtime/bridge.mjs"
logger = get_logger(__name__)


class Compilation(BaseModel):
    model_config = ConfigDict(extra="allow")
    prompt: str
    compiler_version: str


def run_out(path: Path, repo: ReplacementRepository | None = None) -> ReplacementRunOut:
    repo = repo or ReplacementRepository()
    meta = repo.load(path)
    prompt = path / "inputs/prompt.txt"
    return ReplacementRunOut(
        id=meta.id,
        kind=meta.kind,
        target=meta.target,
        status=meta.status,
        archive_path=str(path),
        created_at=meta.created_at,
        prompt=prompt.read_text(encoding="utf-8") if prompt.exists() else "",
        task_id=meta.task_id,
        estimated_credits=meta.estimated_credits,
        credits=meta.credits,
        progress=meta.progress_percent,
        last_error=meta.last_error,
        review_status=meta.review_status,
        output_url=(
            f"/api/v1/templates/replacement-runs/{meta.id}/output"
            if (path / "outputs/generated.mp4").is_file()
            else None
        ),
    )


def list_runs(owner: str) -> list[ReplacementRunOut]:
    repo = ReplacementRepository()
    rows = [run_out(path, repo) for path in repo.all_paths() if repo.load(path).owner_id == owner]
    return sorted(rows, key=lambda row: row.created_at, reverse=True)


def packs(owner: str) -> list[ReplacementPack]:
    return ReplacementRepository().packs(owner)


def prepare(payload: PrepareReplacement, owner: str) -> ReplacementRunOut:
    """同步业务单元，由 HTTP service 的线程池入口调用。"""
    repo = ReplacementRepository()
    if payload.pack_id:
        pack = next((p for p in repo.packs(owner) if p.id == payload.pack_id), None)
        if not pack or pack.kind != payload.kind:
            raise BadRequest("素材包不存在或类型不匹配")
        payload = payload.model_copy(
            update={
                "target": pack.name,
                "reference_ids": pack.reference_ids,
                "wardrobe_mode": pack.wardrobe_mode,
            }
        )
    uid = payload.request_id.hex
    fingerprint = hashlib.sha256((owner + payload.model_dump_json()).encode()).hexdigest()
    with lease(repo.storage.resolve(f"catalog/locks/{uid}.lock")):
        pointer = repo.storage.resolve(f"catalog/runs/{uid}.json")
        if pointer.exists():
            path, meta = repo.owned(uid, owner)
            if meta.fingerprint != fingerprint:
                raise Conflict("请求标识已被不同配置使用，请重新准备")
            if meta.status not in ("preparing", "prepare_failed"):
                return run_out(path, repo)
        else:
            date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y/%m/%d")
            path = repo.storage.resolve(f"runs/{date}/{payload.kind}-{uid}")
            # 修复上一版无索引的半成品：存在真实调用痕迹时绝不覆盖。
            if (path / "run.json").exists():
                previous = repo.load(path)
                if previous.fingerprint != fingerprint or previous.task_id:
                    raise Conflict("已有调用档案，请核对后再继续")
            for folder in ("inputs", "outputs", "records", "review", "code"):
                (path / folder).mkdir(parents=True, exist_ok=True)
            meta = ReplacementManifest(
                id=uid,
                kind=payload.kind,
                target=payload.target,
                owner_id=owner,
                fingerprint=fingerprint,
                status="preparing",
            )
            repo.save(path, meta)
            repo.index(path, uid)
        try:
            _prepare_inputs(repo, path, payload, owner)
            meta = repo.load(path)
            meta.status = "prepared"
            meta.last_error = None
            meta.error_code = None
            meta.file_checksums = {
                p.relative_to(path).as_posix(): checksum(p)
                for p in path.rglob("*")
                if p.is_file()
                and p.name not in ("run.json",)
                and not p.name.endswith((".lock", ".tmp"))
            }
            repo.save(path, meta)
            return run_out(path, repo)
        except Exception:
            logger.exception("替换输入准备失败 run_id=%s", uid)
            meta = repo.load(path)
            meta.status = "prepare_failed"
            meta.error_code = "PREPARATION_FAILED"
            meta.last_error = "输入准备未完成，请检查素材或编译器后用同一配置继续"
            repo.save(path, meta)
            raise


def _validate_inputs(
    repo: ReplacementRepository, payload: PrepareReplacement, owner: str
) -> tuple[StoredMedia, list[StoredMedia]]:
    source = repo.media(payload.source_id, owner)
    refs = [repo.media(value, owner) for value in payload.reference_ids]
    if source.category != "source" or source.type != "video":
        raise BadRequest("请选择已归档的原片")
    if not 1 <= source.duration <= 15 or not source.has_audio:
        raise BadRequest("原片需为带音轨的完整 MP4，时长 1–15 秒；不会自动截断")
    if len(set(payload.reference_ids)) != len(refs):
        raise BadRequest("参考素材不能重复")
    if any(ref.category != payload.kind or ref.type != "image" for ref in refs):
        raise BadRequest("参考图分类需与人物/产品/场景替换类型一致")
    if any(
        "{{" in text or "}}" in text
        for text in (payload.target, payload.notes, payload.replacement_scope)
    ):
        raise BadRequest("请使用普通文字，不要手写素材引用标记")
    if not payload.target.strip() or (
        payload.kind == "product" and not payload.replacement_scope.strip()
    ):
        raise BadRequest("请填写目标名称并选择产品替换范围")
    return source, refs


def _prepare_inputs(
    repo: ReplacementRepository, path: Path, payload: PrepareReplacement, owner: str
) -> None:
    source, refs = _validate_inputs(repo, payload, owner)
    media = [*refs, source]
    bindings: list[dict[str, str]] = []
    roles = {"character": "identity", "product": "target_product", "scene": "target_scene"}
    for index, item in enumerate(media):
        original = repo.storage.resolve(item.path)
        if checksum(original) != item.sha256:
            raise BadRequest("素材字节已变化，请重新登记")
        filename = (
            "reference-video.mp4" if item is source else f"reference-{index + 1}{original.suffix}"
        )
        repo.storage.copy(original, path / "inputs" / filename, item.sha256)
        bindings.append(
            {
                "id": item.id,
                "role": "source_video" if item is source else roles[payload.kind],
                "type": item.type,
                "file": f"inputs/{filename}",
            }
        )
    repo.storage.copy(
        path / "inputs/reference-video.mp4", path / "inputs/source-original.mp4", source.sha256
    )
    variables = {"state_constraints": payload.notes}
    if payload.pack_id:
        pack = next(p for p in repo.packs(owner) if p.id == payload.pack_id)
        variables["state_constraints"] = "\n".join(
            t for t in (pack.features, pack.instructions, payload.notes) if t
        )
        repo.storage.write(path / "inputs/reference-pack.json", pack.model_dump(mode="json"))
    if payload.kind == "character":
        variables.update(
            source_subject="原片唯一前景主角，背景人物不替换",
            identity_description=f"目标人物为{payload.target}，多张参考属于同一人。",
        )
    elif payload.kind == "product":
        variables.update(
            source_product="原片中指定展示的产品",
            target_product=payload.target,
            replacement_scope=payload.replacement_scope,
        )
    else:
        variables.update(
            target_scene=payload.target,
            protected_foreground="原片人物、商品、手持物与所有交互前景对象",
        )
    duration = min(15, max(1, round(source.duration)))
    spec: dict[str, object] = {
        "task": payload.kind,
        "duration": duration,
        "aspect_ratio": payload.aspect_ratio,
        "wardrobe_mode": payload.wardrobe_mode,
        "references": bindings,
        "variables": variables,
    }
    compiled = _compile(spec, payload)
    (path / "inputs/prompt.txt").write_text(compiled.prompt, encoding="utf-8", newline="\n")
    repo.storage.write(path / "inputs/prompt-spec.json", spec)
    repo.storage.write(path / "inputs/configuration.json", payload.model_dump(mode="json"))
    repo.storage.write(
        path / "inputs/input-map.json",
        [
            {**binding, "sha256": item.sha256, "name": item.name}
            for binding, item in zip(bindings, media, strict=True)
        ],
    )
    repo.storage.write(path / "records/prompt-compilation.json", compiled.model_dump())
    repo.storage.write(
        path / "records/media-inputs.json",
        {
            b["file"]: {"width": item.width, "height": item.height, "duration": item.duration}
            for b, item in zip(bindings, media, strict=True)
        },
    )
    if not (path / "review/review.json").exists():
        repo.storage.write(path / "review/review.json", {"status": "pending"})
    shutil.copytree(RUNTIME, path / "code/runtime", dirs_exist_ok=True)
    shutil.copyfile(BRIDGE, path / "code/runtime/bridge.mjs")
    meta = repo.load(path)
    meta.duration = duration
    repo.save(path, meta)
    (path / "00_本次调用说明.md").write_text(
        f"# {payload.target} · {payload.kind}\n\n输入已归档，尚未提交。\n"
        "inputs 保存实际文件副本；outputs 保存原始成片；records 保存状态和错误分类。\n"
        "仅 0 积分提交；人工验收与生成完成分别记录。\n",
        encoding="utf-8",
    )


def _compile(spec: dict[str, object], payload: PrepareReplacement) -> Compilation:
    try:
        result = subprocess.run(
            [get_libtv_settings().node, str(RUNTIME / "workbench/compile.mjs")],
            input=json.dumps(spec),
            capture_output=True,
            encoding="utf-8",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpstreamError("提示词编译器不可用，请检查 Node 配置后继续") from exc
    if result.returncode:
        raise BadRequest("提示词编译失败，请检查参考素材和配置")
    compiled = Compilation.model_validate_json(result.stdout)
    if payload.kind == "character" and payload.wardrobe_mode == "reference":
        compiled.prompt = compiled.prompt.replace("；提供同片原音轨", "；原片内嵌音轨提供声音")
        compiled.prompt = compiled.prompt.replace(
            "声音：为完整原片音轨", "声音：原片内嵌音轨为完整原片音轨"
        )
        compiled.compiler_version += "+embedded-audio-v1"
    return compiled


class ReplacementService:
    def __init__(self) -> None:
        self.repo = ReplacementRepository()

    async def catalog(self, owner: str) -> ReplacementCatalog:
        def load() -> ReplacementCatalog:
            return ReplacementCatalog(
                root=str(self.repo.storage.root()),
                runs=list_runs(owner),
                packs=self.repo.packs(owner),
            )

        return await asyncio.to_thread(load)

    async def prepare(
        self, payload: PrepareReplacement, owner: str, can_read_assets: bool
    ) -> ReplacementRunOut:
        if not can_read_assets:
            raise Forbidden("需要素材读取权限")
        return await asyncio.to_thread(prepare, payload, owner)

    async def detail(self, run_id: str, owner: str) -> ReplacementRunOut:
        def load() -> ReplacementRunOut:
            path, _ = self.repo.owned(run_id, owner)
            return run_out(path, self.repo)

        return await asyncio.to_thread(load)

    async def execute(self, run_id: str, owner: str) -> ReplacementRunOut:
        await schedule(run_id, owner)
        return await self.detail(run_id, owner)

    async def output(self, run_id: str, owner: str) -> Path:
        def load() -> Path:
            path, _ = self.repo.owned(run_id, owner)
            result = path / "outputs/generated.mp4"
            if not result.is_file():
                raise NotFound("原始结果尚未下载完成")
            return result

        return await asyncio.to_thread(load)
