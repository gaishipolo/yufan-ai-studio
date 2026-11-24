"""阿里云百炼 Wan 3.0 视频驱动（北京地域，异步任务 + 计费回执）。

放进 `drivers/` 就自动载入，不改 `.env`、不改 import、不需要注册。
它是模板工作台「替换渠道」的第三个可选渠道（`libtv` / `bailian` / `bailian-prime`）。
2026-11-13 起两个 `bailian-wan3-*` 也公开进 `GET /models`：通用生成入口没有
`archiveRunId` 时，`_submit` 先调 `generic_inputs.materialize()` 补出同款工作区，
后面的 `prepare_inputs` / 回执 / 计费纪律与替换链完全一致。

## 为什么需要「先上传素材」

Wan 只接受**公网 URL** 做参考素材，不认本地文件，所以提交前要把素材直传到
百炼签发的 OSS 临时目录，再把 `oss://...` 直链拼进请求体。这一步不是
`submit`/`poll`/`cancel` 里的任何一个，所以单独一个 `prepare_inputs()`：
**只由本文件的 `submit()` 调用**，没有跨模块的协议（原来的 `InputPreparer`
Protocol 全仓零消费方，2026-11-15 删掉了）。
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
- `poll()` 成功后把成片落到归档工作区的 `outputs/generated.mp4`，`output_url`
  返回 `infra.executor_output.executor_output_url(run_id)` 那个「本地地址」——
  **它没有对应路由**，是驱动与框架之间的内部协议：框架 `persist_output` 认出它，
  把字节搬进对象存储、再换成对象 key 落库。落盘相对路径与地址格式都住在
  `infra/executor_output.py`，两边共用一份（字面量漂移 = 静默丢件）。
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
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from core.exceptions import BadRequest, UpstreamError
from core.prompt_refs import (
    REFERENCE_LABELS,
    REFERENCE_TYPES,
    translate_or_original,
    translate_prompt,
)
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
from infra.executor_output import EXECUTOR_OUTPUT_RELATIVE, executor_output_url
from infra.media_channel import MediaChannel
from modules.model_driver.generic_inputs import materialize
from modules.model_driver.kit.credentials import require_api_key
from modules.model_driver.kit.settings import vendor_settings
from modules.model_driver.receipts import (
    STATUS_REJECTED,
    STATUS_SUBMITTED,
    STATUS_UNKNOWN,
    decide_before_submit,
    recover_external_id,
    settle_submission,
)
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

_PARAM_SCHEMA = {
    # 键名与 `modules/task/params.py` 对齐：前端按 paramSchema 渲染，不加代码。
    "durationSec": {"type": "int", "min": 2, "max": 30, "step": 1, "default": 5},
    "aspectRatio": {
        "type": "enum",
        "options": ["9:16", "16:9", "1:1"],
        "default": "9:16",
    },
}

MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id=MODEL_ID,
        label="百炼万相3.0",
        capability=Capability.VIDEO,
        provider="bailian",
        description="720P，参考视频 + 图 + 音频混合输入",
        param_schema=_PARAM_SCHEMA,
        max_references=10,
        # 百炼吃「参考视频 + 图 + 音频」混合输入。
        reference_kinds=("image", "video", "audio"),
        replacement=True,
    ),
    ModelSpec(
        id=PRIME_MODEL_ID,
        label="百炼万相3.0高速版",
        capability=Capability.VIDEO,
        provider="bailian",
        description="与标准版同参数，出片更快、单价更高",
        param_schema=_PARAM_SCHEMA,
        max_references=10,
        reference_kinds=("image", "video", "audio"),
        replacement=True,
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


# 配置热生效（D3）：mtime 指纹缓存 —— 改 `.env` 下次请求即生效，
# 且不再每次请求都整读一遍 `.env`（原实现靠 `lru_cache`，改配置要重启进程）。
get_bailian_settings = vendor_settings(BailianSettings, env_prefix="BAILIAN")


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

    # 本驱动暴露的标准翻译方法就是模块级 `translate_prompt`（与替换链共享同一
    # 实现，见 test_prompt_translation_is_shared_with_the_replacement_chain）。
    # 走 `translate_or_original` 是统一调用约定：将来若某个渠道没有可翻译的
    # 素材类型，传入 None 就能安全退回原样提示词。
    prompt = translate_or_original(prompt, entries, translate_prompt)
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
    # 参考素材走**百炼签发的 OSS 临时目录**（见 `prepare_inputs`）：素材先由驱动
    # 上传到 OSS，再把 `oss://...` 直链拼进请求体。
    #
    # 为什么只声明 OSS、不加 Base64：百炼的替换链要的是「可访问的素材地址」，
    # Base64 要另改请求构造（`input.media[].url` 换字段），那是行为变更，
    # 留到单独的工作包 —— 这里只把「走哪条通道」显式化，行为保持不变。
    accepted_media_formats = {
        "image": frozenset({MediaChannel.OSS}),
        "video": frozenset({MediaChannel.OSS}),
        "audio": frozenset({MediaChannel.OSS}),
    }
    # 声明 OSS 通道的前提：供应商提供上传凭据端点（`uploads?action=getPolicy`）。
    # 没有它，声明 OSS 就是空头支票 —— 素材会在提交时才发现传不上去。
    oss_upload_supported = True

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
        key = require_api_key(
            self.settings.api_key,
            vendor="百炼",
            env_name="BAILIAN_API_KEY",
            note="北京地域",
        )
        return {"Authorization": f"Bearer {key}"}

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
        run_id = self._ensure_supported(request)
        if request.params.get("archiveRunId") != run_id:
            # 通用入口（工作台 / 模板库）没有替换链在受理期写好的归档，**必须先补档案
            # 再取目录**：`run_path` 读的是归档存储里的指针，还要求目录里已经有
            # `run.json`，而创建这两样正是 `materialize()` 的活
            # （`generic_inputs._ensure_workspace`）。
            #
            # 顺序反过来就是「先取目录、后建目录」：任务受理后 0.1 秒内以
            # `NotFound: 调用档案不存在` 失败，且**一次上游请求都没发**（没计费），
            # 用户只看到一句「提交生成失败」。LibTV 的 `submit` 本来就是先物化
            # 再 `run_path`，这里对齐它（2026-11-16 修）。
            await materialize(request)
        folder = await asyncio.to_thread(run_path, run_id)
        lock = lease(folder / "records/bailian-submit.lock")
        await asyncio.to_thread(lock.__enter__)
        try:
            return await self._submit(request)
        finally:
            await asyncio.to_thread(lock.__exit__, None, None, None)

    @staticmethod
    def _ensure_supported(request: GenerationRequest) -> str:
        """提交前的硬校验：能力 / 模型 / 任务标识。

        放在物化之前 —— 一个不受支持的请求不该先把参考素材下载一遍再被拒绝。
        """
        if request.capability != Capability.VIDEO:
            raise BadRequest("百炼通道仅支持视频生成")
        _real_model(request.model)
        if not request.request_id:
            raise BadRequest("缺少任务标识")
        return request.request_id

    async def _submit(self, request: GenerationRequest) -> str:
        run_id = self._ensure_supported(request)
        folder = await asyncio.to_thread(run_path, run_id)

        # 回执在 DB（2026-11-23 落库）：工作区被删/换机器也能恢复，
        # 不会再因为读不到文件回执而把同一个 run 重复提交给上游（二次计费）。
        # 文件锁仍保留 —— 它管的是**进程内互斥**（含素材上传），和回执是两个职责。
        #
        # 顺序：只读恢复 → 本地校验 → **抢占** → 付费 POST → 结算。
        # 抢占必须在校验之后 —— 否则校验失败会留下 submitting 回执，把后续
        # 合法重试永久锁死（原来的文件回执也是这个顺序）。
        existing = await recover_external_id(
            provider="bailian", run_id=run_id, model=request.model
        )
        if existing is not None:
            return existing
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

        # 校验全部通过，现在才抢占提交权（并发下只能有一个请求进到付费 POST）
        await decide_before_submit(
            provider="bailian",
            run_id=run_id,
            model=request.model,
            channel_used="oss",  # 百炼音频走 OSS；图片将来改内联时按实际通道填
        )
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
        except BadRequest as exc:
            await settle_submission(
                provider="bailian", run_id=run_id, status=STATUS_REJECTED, error=str(exc)
            )
            raise
        except (UpstreamError, ValueError, TypeError) as exc:
            # 可能已受理：记 submission_unknown 并禁止自动重提（DB 状态机守着）
            await settle_submission(
                provider="bailian", run_id=run_id, status=STATUS_UNKNOWN, error=str(exc)
            )
            raise UpstreamError("百炼提交回执未知，请核对上游；禁止自动重提") from exc
        external_id = f"{run_id}:{task_id}"
        await settle_submission(
            provider="bailian",
            run_id=run_id,
            status=STATUS_SUBMITTED,
            task_id=task_id,
            external_id=external_id,
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
            destination = folder / EXECUTOR_OUTPUT_RELATIVE
            if not await asyncio.to_thread(destination.is_file):
                await self._download(output.get("video_url"), destination)
            # 成片先落盘再由替换链发布：这里返回的是**本地地址**（内部协议，
            # 没有对应路由），框架 `persist_output` 会认出它、把字节搬进对象存储。
            return GenerationProgress(
                external_id,
                GenerationState.SUCCEEDED,
                100,
                output_url=executor_output_url(run_id),
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
