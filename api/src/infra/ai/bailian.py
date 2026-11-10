"""Beijing Bailian Wan3 replacement adapter with durable billing receipts.

Only the first submission may create a paid task. An interrupted submission is
left for reconciliation; retries may query a recorded task, never recreate it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from core.exceptions import BadRequest, UpstreamError
from infra.ai.base import (
    Capability,
    GenerationProgress,
    GenerationRequest,
    GenerationState,
    ModelSpec,
)
from infra.archive import get_archive_storage, run_dir
from infra.archive.base import lease
from infra.archive.runs import check_id
from infra.driver_config import DriverSettings, driver_config

MODEL_ID = "bailian-wan3-replacement"
PRIME_MODEL_ID = "bailian-wan3-prime-replacement"
_MODELS = {MODEL_ID: "wan3.0-video", PRIME_MODEL_ID: "wan3.0-video-prime"}
_TYPES = {"video": "reference_video", "image": "reference_image", "audio": "reference_audio"}
_LABELS = {"video": "视频", "image": "图", "audio": "音频"}
_MAX_DOWNLOAD = 1024 * 1024 * 1024


class BailianSettings(DriverSettings):
    model_config = driver_config("BAILIAN")

    base_url: str = "https://llm-cz0qm1j8cl2weo19.cn-beijing.maas.aliyuncs.com/api/v1"
    api_key: str = ""
    timeout_sec: float = 45
    download_timeout_sec: float = 45


@lru_cache
def get_bailian_settings() -> BailianSettings:
    return BailianSettings()


def run_path(run_id: str) -> Path:
    return run_dir(get_archive_storage(), check_id(run_id))


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BadRequest("百炼调用档案缺失或损坏") from exc


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _real_model(model: str) -> str:
    if model not in _MODELS:
        raise BadRequest("不支持的百炼模型")
    return _MODELS[model]


def _local_file(folder: Path, relative: str) -> Path:
    target = (folder / relative).resolve()
    if not target.is_relative_to((folder / "inputs").resolve()):
        raise BadRequest("百炼素材路径无效")
    if not target.is_file():
        raise BadRequest("百炼参考素材文件缺失")
    return target


def _digest(file: Path) -> str:
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _fps(file: Path) -> float | None:
    # Existing materialization records dimensions/duration but not frame rate.
    from infra.media_probe import _probe_stream

    result = re.search(r"([\d.]+) fps", _probe_stream(file))
    return float(result[1]) if result else None


def translate_prompt(prompt: str, entries: list[dict]) -> str:
    """Compile shared Mixed references to Wan's independently numbered media."""
    counts = dict.fromkeys(_TYPES, 0)
    references = {}
    for index, entry in enumerate(entries, 1):
        kind = entry.get("type")
        if kind not in _TYPES:
            raise BadRequest("百炼只支持视频、图片和音频参考素材")
        counts[kind] += 1
        references[f"Mixed {index}"] = f"{_LABELS[kind]}{counts[kind]}"

    def replace(match: re.Match) -> str:
        token = f"Mixed {int(match[1] or match[2])}"
        if token not in references:
            raise BadRequest("提示词引用了不存在的Mixed素材")
        return references[token]

    return re.sub(
        r"\{\{\s*Mixed\s+(\d+)\s*\}\}|(?<![A-Za-z0-9_])Mixed\s+(\d+)(?!\d)",
        replace,
        prompt,
    )


def _validated_inputs(folder: Path, model: str) -> tuple[dict, list[Path], list[str]]:
    entries = _read(folder / "inputs/input-map.json")
    spec = _read(folder / "inputs/prompt-spec.json")
    if not isinstance(entries, list) or not isinstance(spec, dict):
        raise BadRequest("百炼素材清单格式无效")
    prompt = (folder / "inputs/prompt.txt").read_text(encoding="utf-8")
    if len(prompt) > 20000:
        raise BadRequest("百炼提示词不能超过20000字符")
    duration = spec.get("duration", 5)
    if isinstance(duration, bool) or not isinstance(duration, int) or not 2 <= duration <= 30:
        raise BadRequest("百炼输出时长须为2至30秒的整数")
    ratio = spec.get("aspect_ratio", "adaptive")
    if ratio not in {"adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}:
        raise BadRequest("百炼视频画幅无效")
    metadata_file = folder / "records/media-inputs.json"
    metadata = _read(metadata_file) if metadata_file.is_file() else {}
    counts = dict.fromkeys(_TYPES, 0)
    totals = {"video": 0.0, "audio": 0.0}
    files, types = [], []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("type") not in _TYPES:
            raise BadRequest("百炼只支持视频、图片和音频参考素材")
        kind = entry["type"]
        counts[kind] += 1
        if counts[kind] > (10 if kind == "image" else 5):
            raise BadRequest("百炼最多支持10张图片、5段视频和5段音频")
        file = _local_file(folder, str(entry.get("file", "")))
        size_limit = {"video": 100, "image": 20, "audio": 15}[kind]
        if not 0 < file.stat().st_size <= size_limit * 1024 * 1024:
            raise BadRequest(f"百炼{_LABELS[kind]}文件须非空且不超过{size_limit}MB")
        extensions = {
            "video": {".mp4", ".mov"},
            "image": {".jpg", ".jpeg", ".png", ".bmp", ".webp"},
            "audio": {".wav", ".mp3"},
        }
        if file.suffix.lower() not in extensions[kind]:
            raise BadRequest("百炼素材格式不受支持")
        if not entry.get("sha256") or _digest(file) != entry["sha256"]:
            raise BadRequest("百炼素材校验失败，请重新准备素材")
        meta = {**metadata.get(entry["file"], {}), **entry.get("metadata", {})}
        if kind in totals:
            seconds = meta.get("duration", entry.get("duration"))
            if seconds is None or not 1 <= float(seconds) <= 15:
                raise BadRequest("百炼视频和音频须提供1至15秒时长信息")
            totals[kind] += float(seconds)
            if totals[kind] > 15:
                raise BadRequest("百炼视频或音频参考总时长不能超过15秒")
        if kind in {"video", "image"}:
            upper = 4096 if kind == "video" else 8000
            for dimension in ("width", "height"):
                if not 240 <= int(meta.get(dimension) or 0) <= upper:
                    raise BadRequest(f"百炼{_LABELS[kind]}单边尺寸须在240至{upper}像素之间")
            width, height = int(meta.get("width") or 0), int(meta.get("height") or 0)
            if max(width, height) / min(width, height) > 8:
                raise BadRequest("百炼素材长宽比不能超过8:1")
        if kind == "video":
            fps = meta.get("fps") or entry.get("fps")
            fps = float(fps) if fps is not None else _fps(file)
            if fps is None or fps < 16:
                raise BadRequest("百炼视频帧率必须可验证且不能低于16fps")
        files.append(file)
        types.append(_TYPES[kind])
    if duration + totals["video"] > 30:
        raise BadRequest("百炼输入视频与输出视频总时长不能超过30秒")

    prompt = translate_prompt(prompt, entries)
    if not prompt.strip() and not files:
        raise BadRequest("百炼提示词和素材不能同时为空")
    payload = {
        "model": _real_model(model),
        # Wan rejects an empty media array: text-to-video must omit the field entirely.
        "input": {"prompt": prompt},
        "parameters": {
            "duration": duration,
            "ratio": ratio,
            "resolution": "720P",
            "prompt_extend": False,
            "audio": True,
        },
    }
    return payload, files, types


class BailianProvider:
    name = "bailian"
    capabilities = frozenset({Capability.VIDEO})
    supports_poll = True
    models = tuple(
        ModelSpec(id=model, label=label, capability=Capability.VIDEO, provider="bailian")
        for model, label in ((MODEL_ID, "百炼万相3.0"), (PRIME_MODEL_ID, "百炼万相3.0高速版"))
    )

    def __init__(
        self,
        *,
        settings: BailianSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_bailian_settings()
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self.transport, timeout=self.settings.timeout_sec, trust_env=False
        )

    def _headers(self) -> dict[str, str]:
        if not self.settings.api_key:
            raise BadRequest("请先配置北京地域的BAILIAN_API_KEY")
        return {"Authorization": f"Bearer {self.settings.api_key}"}

    def _url(self, path: str) -> str:
        return self.settings.base_url.rstrip("/") + path

    async def _json(self, client: httpx.AsyncClient, method: str, path: str, **kwargs) -> dict:
        try:
            response = await client.request(method, self._url(path), **kwargs)
            if 400 <= response.status_code < 500 and response.status_code != 408:
                raise BadRequest(f"百炼拒绝请求（HTTP {response.status_code}），请核对配置和素材")
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or data.get("code"):
                raise ValueError("invalid response")
            return data
        except (httpx.HTTPError, ValueError) as exc:
            raise UpstreamError("百炼接口暂不可用，请核对调用档案") from exc

    async def prepare_inputs(self, run_id: str, model: str = MODEL_ID) -> dict:
        _real_model(model)
        headers = self._headers()
        folder = await asyncio.to_thread(run_path, run_id)
        payload, files, types = await asyncio.to_thread(_validated_inputs, folder, model)
        media: list[dict] = []
        async with self._client() as client:
            for file, kind in zip(files, types, strict=True):
                result = await self._json(
                    client,
                    "GET",
                    "/uploads",
                    headers=headers,
                    params={"action": "getPolicy", "model": payload["model"]},
                )
                try:
                    policy = result["data"]
                    key = f"{policy['upload_dir'].rstrip('/')}/{uuid.uuid4().hex}{file.suffix}"
                    host = policy["upload_host"]
                    if urlsplit(host).scheme != "https":
                        raise ValueError("invalid upload host")
                    fields = {
                        "OSSAccessKeyId": policy["oss_access_key_id"],
                        "Signature": policy["signature"],
                        "policy": policy["policy"],
                        "x-oss-object-acl": policy["x_oss_object_acl"],
                        "x-oss-forbid-overwrite": policy["x_oss_forbid_overwrite"],
                        "key": key,
                        "success_action_status": "200",
                    }

                    # Reading local files and multipart encoding must not block the loop.
                    def upload(file=file, host=host, fields=fields) -> None:
                        with (
                            httpx.Client(
                                timeout=self.settings.timeout_sec, trust_env=False
                            ) as sync_client,
                            file.open("rb") as stream,
                        ):
                            response = sync_client.post(
                                host, data=fields, files={"file": (file.name, stream)}
                            )
                            response.raise_for_status()

                    if self.transport is None:
                        await asyncio.to_thread(upload)
                    else:
                        content = await asyncio.to_thread(file.read_bytes)
                        response = await client.post(
                            host, data=fields, files={"file": (file.name, content)}
                        )
                        response.raise_for_status()
                except (KeyError, TypeError, ValueError, OSError, httpx.HTTPError) as exc:
                    raise UpstreamError("百炼素材上传失败，请重新准备素材") from exc
                media.append({"type": kind, "url": f"oss://{key}"})
        if media:
            payload["input"]["media"] = media
        await asyncio.to_thread(_write, folder / "records/bailian-request.json", payload)
        return payload

    async def submit(self, request: GenerationRequest) -> str:
        folder = await asyncio.to_thread(run_path, request.request_id)
        lock = lease(folder / "records/bailian-submit.lock")
        await asyncio.to_thread(lock.__enter__)
        try:
            return await self._submit(request)
        finally:
            await asyncio.to_thread(lock.__exit__, None, None, None)

    async def _submit(self, request: GenerationRequest) -> str:
        if request.capability != Capability.VIDEO:
            raise BadRequest("百炼通道仅支持视频生成")
        _real_model(request.model)
        run_id = request.request_id
        if not run_id or request.params.get("archiveRunId") != run_id:
            raise BadRequest("百炼调用档案标识不匹配")
        folder = await asyncio.to_thread(run_path, run_id)
        receipt_file = folder / "records/bailian-submission.json"
        if await asyncio.to_thread(receipt_file.exists):
            receipt = await asyncio.to_thread(_read, receipt_file)
            if receipt.get("task_id"):
                if receipt.get("model") != request.model:
                    raise BadRequest("百炼已提交任务的模型不能变更")
                return f"{run_id}:{receipt['task_id']}"
            if receipt.get("status") == "rejected":
                await asyncio.to_thread(
                    receipt_file.rename,
                    receipt_file.with_name(f"bailian-rejected-{uuid.uuid4().hex}.json"),
                )
            else:
                raise UpstreamError("百炼提交回执未知，请核对上游；禁止自动重提")
        headers = {
            **self._headers(),
            "X-DashScope-Async": "enable",
            "X-DashScope-OssResourceResolve": "enable",
        }
        prepared = folder / "records/bailian-request.json"
        if not await asyncio.to_thread(prepared.exists):
            payload = await self.prepare_inputs(run_id, request.model)
        else:
            payload = await asyncio.to_thread(_read, prepared)
        if payload.get("model") != _real_model(request.model):
            raise BadRequest("百炼素材绑定模型不匹配，请重新准备素材")
        prompt = payload.get("input", {}).get("prompt")
        if not isinstance(prompt, str) or len(prompt) > 20000:
            raise BadRequest("百炼提示词不能超过20000字符")
        duration = payload.get("parameters", {}).get("duration")
        if isinstance(duration, bool) or not isinstance(duration, int) or not 2 <= duration <= 30:
            raise BadRequest("百炼输出时长须为2至30秒的整数")
        if time.time() - (await asyncio.to_thread(prepared.stat)).st_mtime > 47 * 3600:
            raise BadRequest("百炼临时素材已过期，请重新准备素材")
        receipt = {"status": "submitting", "model": request.model, "run_id": run_id}

        def claim() -> None:
            try:
                with receipt_file.open("x", encoding="utf-8") as stream:
                    json.dump(receipt, stream)
            except FileExistsError as exc:
                raise UpstreamError("百炼已有提交记录；禁止自动重提") from exc

        await asyncio.to_thread(claim)
        try:
            async with self._client() as client:
                result = await self._json(
                    client,
                    "POST",
                    "/services/aigc/video-generation/video-synthesis",
                    headers=headers,
                    json=payload,
                )
            task_id = result.get("output", {}).get("task_id")
            if not isinstance(task_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", task_id):
                raise ValueError("missing task id")
        except BadRequest:
            await asyncio.to_thread(_write, receipt_file, {**receipt, "status": "rejected"})
            raise
        except (UpstreamError, ValueError, TypeError) as exc:
            await asyncio.to_thread(
                _write, receipt_file, {**receipt, "status": "submission_unknown"}
            )
            raise UpstreamError("百炼提交回执未知，请核对上游；禁止自动重提") from exc
        external_id = f"{run_id}:{task_id}"
        await asyncio.to_thread(
            _write,
            receipt_file,
            {**receipt, "status": "submitted", "task_id": task_id, "external_id": external_id},
        )
        return external_id

    async def poll(self, external_id: str, *, model: str = "") -> GenerationProgress:
        run_id, _, task_id = external_id.partition(":")
        if not run_id or not re.fullmatch(r"[a-zA-Z0-9_-]+", task_id):
            raise BadRequest("百炼上游任务标识无效")
        folder = await asyncio.to_thread(run_path, run_id)
        async with self._client() as client:
            result = await self._json(client, "GET", f"/tasks/{task_id}", headers=self._headers())
        output = result.get("output", {})
        status = output.get("task_status")
        if status == "SUCCEEDED":
            destination = folder / "outputs/generated.mp4"
            if not await asyncio.to_thread(destination.is_file):
                await self._download(output.get("video_url"), destination)
            # ReplacementStore publishes this file before committing the business success state.
            return GenerationProgress(
                external_id,
                GenerationState.SUCCEEDED,
                100,
                output_url=f"/api/v1/templates/replacement-runs/{run_id}/output",
            )
        if status in {"FAILED", "CANCELED", "UNKNOWN"}:
            return GenerationProgress(
                external_id,
                GenerationState.FAILED,
                error="百炼任务失败或已失效，请核对上游任务档案",
            )
        if status not in {"PENDING", "RUNNING"}:
            raise UpstreamError("百炼返回未知任务状态，请稍后继续查询")
        return GenerationProgress(
            external_id, GenerationState.QUEUED if status == "PENDING" else GenerationState.RUNNING
        )

    async def _download(self, url: str | None, destination: Path) -> None:
        if not url or urlsplit(url).scheme != "https":
            raise UpstreamError("百炼未返回有效成片下载地址")
        temporary = destination.with_name(f"generated.{uuid.uuid4().hex}.part")
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
        try:
            async with asyncio.timeout(self.settings.download_timeout_sec):
                async with (
                    self._client() as client,
                    client.stream("GET", url, follow_redirects=True) as response,
                ):
                    response.raise_for_status()
                    size = 0
                    stream = await asyncio.to_thread(temporary.open, "wb")
                    try:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            size += len(chunk)
                            if size > _MAX_DOWNLOAD:
                                raise ValueError("download too large")
                            await asyncio.to_thread(stream.write, chunk)
                    finally:
                        await asyncio.to_thread(stream.close)
                    if not size:
                        raise ValueError("empty download")
            await asyncio.to_thread(temporary.replace, destination)
        except (httpx.HTTPError, OSError, ValueError, TimeoutError) as exc:
            raise UpstreamError("百炼成片下载未完成，请继续查询已有任务") from exc
        finally:
            await asyncio.to_thread(temporary.unlink, missing_ok=True)

    async def cancel(self, external_id: str) -> None:
        raise BadRequest("百炼任务请到官方控制台核对和取消")


async def prepare_inputs(run_id: str, model: str = MODEL_ID) -> dict:
    return await BailianProvider().prepare_inputs(run_id, model)


Driver = BailianProvider
