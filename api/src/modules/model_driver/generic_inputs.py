"""把通用生成请求物化成替换链同款的归档工作区。

## 为什么需要这一层

`drivers/libtv.py` 与 `drivers/bailian.py` 的 `submit` 都以**归档工作区**为输入
契约：`run.json` 的状态机、`inputs/input-map.json` 的素材清单、
`inputs/prompt-spec.json` 的参数、`records/media-inputs.json` 的宽高时长。
那套档案原先只有 `modules/template` 的四步工作台会写，所以三个替换模型
只能标 `internal=True`——放进 `GET /models` 让用户选中必然 400。

整体迁移之后要让它们在生成工作台（`/tasks`）也能用，有两条路：把两个驱动
的提交逻辑各重写一遍，或者**把通用输入补成它们认得的形状**。这里选后者：
新增一个物化器，驱动只在「没有 `archiveRunId`」时先调它，后面的提交、
回执、去重、计费纪律全部原样复用。模板库那条链带上 `archiveRunId`，
走的仍是自己写的档案，一行都不改。

## 两家读的是同一份档案，但不是同一批字段

- **百炼**（`drivers/bailian.py::_validated_inputs`）读 `input-map.json` 的
  `type/file/sha256/metadata/duration/fps`、`prompt-spec.json` 的
  `duration/aspect_ratio`、`prompt.txt`、`media-inputs.json`；
- **LibTV**（`runtime/bridge.mjs` 的 `upload` 阶段）还要 `prompt-spec.json`
  的 `references`、`records/prompt-compilation.json` 的 `prompt/bindings`，
  并且 `canvas-run.mjs::update()` 会遍历 **inputs/outputs/records/review/code**
  五个目录算校验和——少建一个目录，`readdir` 直接 ENOENT。

所以这一份档案必须同时满足两边；只按百炼的形状写，LibTV 会在「上传准备」
阶段炸在一个和用户输入毫无关系的路径错误上。

## 输入从哪来

`VideoTaskStore.load()` 把受理期校验过的资产库引用冻结进
`GenerationRecord.params["referenceKeys"]`（`{id, key, kind, name}`），
`build_request` 原样透传。物化器只认这批 key，不查库、不认用户 id：
权限与存在性在 `POST /tasks` 受理时就判完了（`core/library_refs.py`）。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from core.exceptions import BadRequest, NotFound
from infra import get_storage
from infra.ai.base import GenerationRequest
from infra.archive import get_archive_storage, save_pointer
from infra.archive.base import write_json
from infra.archive.runs import check_id, relative_key, runs_root
from infra.media_probe import encode_text, inspect
from infra.storage.base import StorageError

__all__ = ["materialize"]

# `canvas-run.mjs::update()` 会遍历 inputs/outputs/records/review/code 算 sha256；
# 缺一个目录就会在第一次状态更新时 ENOENT。`logs` 是监督进程写 stderr 的地方。
_FOLDERS = ("inputs", "outputs", "records", "logs", "review", "code")
_KINDS = frozenset({"image", "video", "audio"})
_SUFFIXES = {
    "image": frozenset({".png", ".jpg", ".jpeg", ".webp"}),
    "video": frozenset({".mp4"}),
    "audio": frozenset({".mp3", ".wav"}),
}
_DEFAULT_SUFFIX = {"image": ".png", "video": ".mp4", "audio": ".wav"}
_RATIOS = frozenset({"adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"})


def _suffix(ref: Mapping[str, Any]) -> str:
    """后缀按 kind 收窄：文件名叫 `clip.webp` 的视频不能当图片去 probe。"""
    kind = str(ref.get("kind", ""))
    if kind not in _KINDS:
        raise BadRequest(f"不支持的参考素材类型：{kind or '未知'}")
    suffix = Path(str(ref.get("name") or "")).suffix.lower()
    return suffix if suffix in _SUFFIXES[kind] else _DEFAULT_SUFFIX[kind]


def _ensure_workspace(run_id: str) -> Path:
    """建目录 + 指针。指针不能省：`executor.run_path()` 靠它定位档案。"""
    folder = runs_root() / run_id
    for name in _FOLDERS:
        (folder / name).mkdir(parents=True, exist_ok=True)
    save_pointer(get_archive_storage(), run_id, relative_key(folder))
    return folder


def _reference_index(
    refs: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """先算出清单再落盘：参数 / 类型校验应该发生在写文件之前。"""
    return [
        {
            "id": str(ref.get("id") or f"ref-{index}"),
            "type": str(ref.get("kind") or ""),
            "file": f"inputs/{index}{_suffix(ref)}",
        }
        for index, ref in enumerate(refs)
    ]


def _prompt_spec(
    request: GenerationRequest, references: Sequence[dict[str, str]]
) -> dict[str, Any]:
    params = request.params
    raw_duration = params.get("durationSec", 5)
    try:
        duration = int(raw_duration)
    except (TypeError, ValueError) as exc:
        raise BadRequest("输出时长必须是整数秒") from exc
    if isinstance(raw_duration, bool) or not 2 <= duration <= 30:
        raise BadRequest("输出时长须为 2 至 30 秒的整数")
    ratio = str(params.get("aspectRatio") or "9:16")
    if ratio not in _RATIOS:
        raise BadRequest(f"不支持的画幅 {ratio!r}")
    return {"duration": duration, "aspect_ratio": ratio, "references": list(references)}


def _write_inputs(
    folder: Path,
    refs: Sequence[Mapping[str, Any]],
    blobs: Sequence[bytes],
    references: Sequence[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    metadata: dict[str, dict[str, Any]] = {}
    for ref, data, item in zip(refs, blobs, references, strict=True):
        relative = item["file"]
        target = folder / relative
        target.write_bytes(data)
        probe = inspect(target, target.suffix.lower())
        # 视频/音频的素材时长必须落在 1–15 秒；输出时长由 prompt-spec 单独给。
        duration = (
            round(float(probe["duration"]), 3) if item["type"] in {"video", "audio"} else 0.0
        )
        # `fps` 故意不给值：百炼的 `_validated_inputs` 在缺 fps 时会自己跑一次
        # ffprobe。写死 24 等于替用户伪造一份「帧率合规」的证据。
        meta = {
            "width": probe["width"],
            "height": probe["height"],
            "duration": duration,
            "fps": 0,
        }
        metadata[relative] = meta
        entries.append(
            {
                "id": item["id"],
                "name": str(ref.get("name") or ""),
                "type": item["type"],
                "file": relative,
                "sha256": hashlib.sha256(data).hexdigest(),
                "metadata": meta,
                "duration": duration,
                "fps": 0,
            }
        )
    return entries, metadata


def _write_everything(
    folder: Path,
    run_id: str,
    request: GenerationRequest,
    spec: dict[str, Any],
    entries: list[dict[str, Any]],
    metadata: dict[str, dict[str, Any]],
    references: list[dict[str, str]],
) -> None:
    prompt = request.prompt.strip()
    # 走 `encode_text`：它是所有文本落盘的唯一出口，NUL 在这里被拦掉，
    # 不会留下一份「写了一半」的工作区再去撞 PG 的 text 列。
    prompt_bytes = encode_text(prompt)
    (folder / "inputs" / "prompt.txt").write_bytes(prompt_bytes)
    write_json(folder / "inputs" / "prompt-spec.json", spec)
    write_json(folder / "inputs" / "input-map.json", entries)
    write_json(folder / "records" / "media-inputs.json", metadata)
    write_json(
        folder / "records" / "prompt-compilation.json",
        {
            "prompt": prompt,
            # `bridge.mjs` 的 upload 阶段按 index 比对 spec.references 与
            # compiled.bindings 的 id/type，两处必须是同一批、同一顺序。
            "bindings": [dict(item) for item in references],
            "compiler_version": "generic-inputs-v1",
            "status": "compiled",
        },
    )
    # `status=uploaded` 是执行器唯一肯提交的入口状态；`preparation_ready`
    # 与 `file_checksums` 是桥接层复核输入没被换过的证据。
    write_json(
        folder / "run.json",
        {
            "id": run_id,
            "status": "uploaded",
            "preparation_ready": True,
            "state_version": 1,
            "framework_status": "queued",
            "billing_mode": "free",
            "model": request.model,
            "resolution": "720P",
            "duration": spec["duration"],
            "file_checksums": {item["file"]: item["sha256"] for item in entries},
            "prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
        },
    )


async def materialize(request: GenerationRequest) -> None:
    """把一次通用提交补成归档形状；重复调用幂等（重试会走同一份回执）。"""
    try:
        run_id = check_id(request.request_id)
    except NotFound as exc:
        raise BadRequest("该模型的归档工作区要求 32 位任务标识，当前记录不兼容") from exc
    raw_refs = request.params.get("referenceKeys") or []
    if not isinstance(raw_refs, Sequence) or isinstance(raw_refs, (str, bytes)):
        raise BadRequest("参考素材清单格式无效")
    refs = [item for item in raw_refs if isinstance(item, Mapping)]
    if len(refs) != len(raw_refs):
        raise BadRequest("参考素材清单格式无效")

    # 类型与参数先校验：写了一半的工作区再发现「时长 99 秒」很难清理，
    # 而且参数错误本该在受理期就报给用户。
    references = _reference_index(refs)
    spec = _prompt_spec(request, references)

    folder = await asyncio.to_thread(_ensure_workspace, run_id)
    # 已经物化过（框架重试 / 启动恢复）就别重新下载一遍：归档里的回执可能
    # 已经写下了上游 task_id，重写会把它抹掉，触发二次提交。
    if (folder / "run.json").is_file():
        return

    storage = get_storage()
    blobs: list[bytes] = []
    for ref in refs:
        key = str(ref.get("key") or "")
        if not key:
            raise BadRequest("参考素材缺少存储键")
        try:
            data = await storage.get(key)
        except (StorageError, OSError) as exc:
            # `get()` 在对象不存在时抛 `StorageError`，不是返回空 bytes；
            # 让它冒出去会变成 500「服务端错误」，用户只知道「失败」却不知道重选素材。
            raise BadRequest("参考素材字节已丢失，请重新选择素材") from exc
        if not data:
            raise BadRequest("参考素材字节已丢失，请重新选择素材")
        blobs.append(data)
    entries, metadata = await asyncio.to_thread(
        _write_inputs, folder, refs, blobs, references
    )
    await asyncio.to_thread(
        _write_everything, folder, run_id, request, spec, entries, metadata, references
    )
