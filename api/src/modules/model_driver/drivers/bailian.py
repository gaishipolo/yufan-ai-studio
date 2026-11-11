"""阿里云百炼 Wan 3.0 视频驱动（北京地域，异步任务 + 计费回执）。

放进 `drivers/` 就自动载入，**不需要**改 `.env` 的 `AI_PROVIDERS`、不需要注册。
它是模板工作台「替换渠道」的第三个可选渠道（`libtv` / `bailian` / `bailian-prime`），
`bailian-wan3-*` 这两个模型 id 只在那条链里用，通用生成入口用 `agnes-*`。

## 为什么需要「先上传素材」

Wan 只接受**公网 URL** 做参考素材，不认本地文件，所以提交前要把素材直传到
百炼签发的 OSS 临时目录，再把 `oss://...` 直链拼进请求体。这一步不是
`submit`/`poll`/`cancel` 里的任何一个，所以本驱动另外实现可选协议
`infra.ai.base.InputPreparer`：替换链拿到实例后按类型判断，有就调、没有就跳过。
libtv 不需要这一步（它跑本地执行器，自己处理文件）。

## 计费纪律：只有第一次提交能花钱

一条 run 的上游任务号落盘在 `records/bailian-submission.json`：

- 提交前用 `O_EXCL` 抢占（`records/bailian-submit.lock` + 回执文件），
  抢不到就是「已有提交记录」；
- 4xx（供应商明确拒绝）记 `rejected`，允许人工继续；
- 超时 / 5xx / 响应畸形记 `submission_unknown`，**一律禁止自动重提**——
  请求可能已经被受理，重提就是二次计费。恢复流程靠 `_restore_bailian_receipt()`
  从回执里读回 task_id 继续查询。

## 与替换链的耦合（迁进 `drivers/` 时保留，未做抽象）

- 素材与请求体的路径取自 run 工作区（`infra.archive.run_dir`）：
  `inputs/input-map.json`、`inputs/prompt-spec.json`、`records/media-inputs.json`。
  这些是替换链编排期写下的档案格式，不是本驱动自定义的。
- `poll()` 成功后把成片落到 `outputs/generated.mp4`，`output_url` 指向
  `templates` 模块的下载路由——框架只把它当字符串落库，路由归属仍是替换链。
- 归档租约、回执归档（`bailian-reconfirmed-*.json`）由替换链的 worker 负责，
  驱动只保证「回执写得下、读得回、不重复提交」。

## 契约

    GET  /uploads?action=getPolicy&model=...        → OSS 直传凭据
    POST {upload_host}                              → 直传（不带 Authorization）
    POST /services/aigc/video-generation/video-synthesis
         X-DashScope-Async: enable
         X-DashScope-OssResourceResolve: enable     → {output: {task_id}}
    GET  /tasks/{task_id}                           → {output: {task_status, video_url}}

上游约定的素材约束（单边尺寸、时长上限、帧率、格式）都在 `_validated_inputs`
里前置校验：这些错误上传完才会被拒，而那时素材已经传完、用户只看到一句
「上传失败」，不知道是时长超了还是帧率不可验证。
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
from core.prompt_refs import REFERENCE_LABELS, REFERENCE_TYPES, translate_prompt
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
from modules.model_driver.spec import DriverMeta

META = DriverMeta(
    label="阿里云百炼",
    description="Wan 3.0 视频（北京地域），参考素材先直传 OSS 再提交",
    version="1.0.0",
    author="",
)

MODEL_ID = "bailian-wan3-replacement"
PRIME_MODEL_ID = "bailian-wan3-prime-replacement"
_MODELS = {MODEL_ID: "wan3.0-video", PRIME_MODEL_ID: "wan3.0-video-prime"}

MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id=MODEL_ID,
        label="百炼万相3.0",
        capability=Capability.VIDEO,
        provider="bailian",
        description="替换渠道：720P，参考视频 + 图 + 音频混合输入",
        # 替换链专用（提交要带 `archiveRunId`，见 `submit` 里的标识校验）。
        # 不在目录里露脸，避免用户在通用视频页选中后 400。
        internal=True,
    ),
    ModelSpec(
        id=PRIME_MODEL_ID,
        label="百炼万相3.0高速版",
        capability=Capability.VIDEO,
        provider="bailian",
        description="替换渠道：与标准版同参数，出片更快、单价更高",
        internal=True,
    ),
)

_MAX_DOWNLOAD = 1024 * 1024 * 1024
# 提交正常只有一次 HTTP 往返；但「prepare 没跑过」时提交里会兜底上传素材
# （100MB 视频 + 15MB 音频），所以预算要给到上传的量级。框架会再加 30 秒余量，
# 到点取消 handler 会让任务变成「有提交意图、没有任务号」= 结果未知、禁止重提。
_SUBMIT_BUDGET_SEC = 600.0


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
    # 归档记录里有宽高与时长，没有帧率，只能现探一次。
    from infra.media_probe import _probe_stream

    result = re.search(r"([\d.]+) fps", _probe_stream(file))
    return float(result[1]) if result else None


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
    counts = dict.fromkeys(REFERENCE_TYPES, 0)
    totals = {"video": 0.0, "audio": 0.0}
    files, types = [], []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("type") not in REFERENCE_TYPES:
            raise BadRequest("百炼只支持视频、图片和音频参考素材")
        kind = entry["type"]
        counts[kind] += 1
        if counts[kind] > (10 if kind == "image" else 5):
            raise BadRequest("百炼最多支持10张图片、5段视频和5段音频")
        file = _local_file(folder, str(entry.get("file", "")))
        size_limit = {"video": 100, "image": 20, "audio": 15}[kind]
        if not 0 < file.stat().st_size <= size_limit * 1024 * 1024:
            raise BadRequest(f"百炼{REFERENCE_LABELS[kind]}文件须非空且不超过{size_limit}MB")
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
                    raise BadRequest(
                        f"百炼{REFERENCE_LABELS[kind]}单边尺寸须在240至{upper}像素之间"
                    )
            width, height = int(meta.get("width") or 0), int(meta.get("height") or 0)
            if max(width, height) / min(width, height) > 8:
                raise BadRequest("百炼素材长宽比不能超过8:1")
        if kind == "video":
            fps = meta.get("fps") or entry.get("fps")
            fps = float(fps) if fps is not None else _fps(file)
            if fps is None or fps < 16:
                raise BadRequest("百炼视频帧率必须可验证且不能低于16fps")
        files.append(file)
        types.append(REFERENCE_TYPES[kind])
    if duration + totals["video"] > 30:
        raise BadRequest("百炼输入视频与输出视频总时长不能超过30秒")

    prompt = translate_prompt(prompt, entries)
    if not prompt.strip() and not files:
        raise BadRequest("百炼提示词和素材不能同时为空")
    payload = {
        "model": _real_model(model),
        # Wan 不接受空的 media 数组：纯文生视频必须整个字段不出现。
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
    """异步视频驱动：`submit` 只拿到 task_id，结果靠 `poll` 收。"""

    name = "bailian"
    capabilities = frozenset({Capability.VIDEO})
    models = MODELS
    supports_poll = True
    # 不声明 `preferred_for`：替换渠道的模型不该抢通用生成页的默认视频模型，
    # 默认值仍由 echo/libtv 那侧的决定。这里只是「可被显式选中」。
    submit_timeout_sec = _SUBMIT_BUDGET_SEC

    def __init__(
        self,
        *,
        settings: BailianSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_bailian_settings()
        self.transport = transport

    # ------------------------------------------------------ 可选协议：素材准备
    def unavailable_reason(self) -> str:
        return "" if self.settings.api_key.strip() else "服务端尚未配置百炼密钥"

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

                    # 读本地文件 + 组 multipart 是阻塞操作，不能占着事件循环。
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

    # ------------------------------------------------------------------ 提交
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

    # ------------------------------------------------------------------ 轮询
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
            # 成片先落盘再由替换链发布：这里返回的是**下载路由**，不是 OSS 直链。
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


Driver = BailianProvider
