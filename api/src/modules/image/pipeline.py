"""生图流水线：把 `image_jobs` 表适配到通用生成框架。

生图供应商有两类形态，这个文件对两者都成立：

- **同步**（Agnes）：`submit` 直接返回终态，框架不轮询，`mark_progress` 不会被调用；
- **异步**：返回任务 ID，框架按节奏轮询并写进度。

所以 Store 的七个方法要按「异步」的完整语义实现——同步供应商只是用不到其中几个。
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging import get_logger
from core.ports.library_ingest import IngestRequest
from core.ports.library_lookup import LibraryAssetKind
from generation.pipeline import register_pipeline
from generation.spec import (
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    GenerationRecord,
)
from infra import get_storage
from infra.ai.base import Capability, GenerationRequest
from infra.driver_config import DriverSettings, driver_config
from modules.image.models import ImageJob

logger = get_logger(__name__)

KIND_IMAGE = "image"

# 一旦记录进入终态，只有业务侧的 `ImageService.retry()` 能复位。
# 框架里任何迟到的提交/轮询回调都不许把它覆盖回去（R11-02 现象 C）。
_TERMINAL = frozenset({STATUS_SUCCEEDED, STATUS_FAILED})

# 成图落盘的下载策略。没配 IMAGE_ASSET_ALLOWED_HOSTS 时只挡**非公网 IP 字面量**
# 与明显的内网域名：任意公网域名（含解析到内网的 nip.io 之类）仍会放行，
# 因为它不解析 DNS。生产环境必须配白名单，`ensure_prod_driver_settings`
# 会在启动期断言（R12-04）。
_ALLOWED_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
_MAX_REDIRECTS = 2
_CHUNK = 64 * 1024
_BLOCKED_HOSTNAMES = frozenset(
    {"localhost", "metadata", "metadata.google.internal", "instance-data"}
)
_BLOCKED_SUFFIXES = (".localhost", ".internal", ".local", ".localdomain")


class ImageAssetSettings(DriverSettings):
    """成图落盘的部署参数（前缀 `IMAGE_ASSET_`）。

    这些是「换部署才变」的驱动参数，按约定放 DriverSettings 而不是 core/config.py
    （手册 §3：驱动自己的配置写在驱动自己的文件里）。
    """

    model_config = driver_config("IMAGE_ASSET")

    # 生产环境必须显式给出供应商 CDN 域名，不许依赖黑名单兜底
    prod_required_fields: ClassVar[tuple[str, ...]] = ("allowed_hosts",)

    # 逗号分隔的域名白名单；留空时只拒绝非公网 IP 字面量与明显的内网域名
    allowed_hosts: str = ""
    max_bytes: int = 20 * 1024 * 1024
    # 这是**下载**超时，不是提交耗时，所以不声明 submit_budget_for
    timeout_sec: float = 60.0


def get_image_asset_settings() -> ImageAssetSettings:
    return ImageAssetSettings()


async def _guard(session: AsyncSession, record_id: str) -> ImageJob | None:
    """终态保护：迟到的回调不许改写已经 concluded 的记录。

    必须先 `refresh` 再判断：SQLAlchemy 的 identity map 会直接返回本 session
    缓存的旧对象，另一个 worker 已经提交的 `failed` 在这里是读不到的，
    不刷新就等于没有保护（R11-02 现象 C 的状态来回跳正是这么发生的）。

    但 `refresh()` 会 expire 并从库里重载，**把本会话尚未提交的改动一并丢弃**。
    `handle_submit` 的「确定没送达」分支正好是两次连续写
    （`clear_submit_attempt` → `_fail`），第二次 `_guard` 会把第一次清掉的标记
    又读回来（R12-02）。所以刷新前先 `flush()`：本会话的改动写进当前事务，
    `refresh` 在同一事务里读得到它，同时也读得到别的 worker 已提交的终态。
    """
    job = await session.get(ImageJob, record_id)
    if job is None:
        return None
    await session.flush()
    await session.refresh(job)
    return None if job.status in _TERMINAL else job


class ImageJobStore:
    """`image_jobs` ↔ `GenerationRecord` 的映射。不负责 commit，由框架统一提交。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _get(self, record_id: str) -> ImageJob | None:
        return await self.session.get(ImageJob, record_id)

    @staticmethod
    def _to_record(job: ImageJob) -> GenerationRecord:
        return GenerationRecord(
            id=job.id,
            kind=KIND_IMAGE,
            model=job.model_id or job.model,
            prompt=job.prompt,
            status=job.status,
            owner_id=job.owner_id,
            params={**job.params, "size": job.size, "count": job.count},
            external_id=job.external_id,
            progress=job.progress,
            output_url=job.output_url,
            cover_url=job.cover_url or None,
            error=job.error_message,
            created_at=job.created_at,
            submit_attempts=job.submit_attempts,
            submit_mode=job.submit_mode,
        )

    async def load(self, record_id: str) -> GenerationRecord | None:
        job = await self._get(record_id)
        return None if job is None else self._to_record(job)

    async def list_unfinished(
        self, session: AsyncSession, limit: int
    ) -> Sequence[GenerationRecord]:
        """框架恢复器用的可选钩子：捞未终态记录（`generation/recovery.py`）。

        按创建时间排序并以主键收尾，分页边界才稳定；`limit` 下推到 SQL，
        供应商故障后不会因为堆积把整张表读进内存。

        **声明了 `recovery_kinds` 却没实现这个方法，恢复会静默地什么也不做**——
        框架只认 pipeline 上的这个可选钩子，缺了就跳过，不报错（见走查文档 G10）。
        """
        rows = (
            await session.scalars(
                select(ImageJob)
                .where(ImageJob.status.in_((STATUS_QUEUED, STATUS_RUNNING)))
                .order_by(ImageJob.created_at, ImageJob.id)
                .limit(limit)
            )
        ).all()
        return [self._to_record(row) for row in rows]

    async def mark_submitting(self, record_id: str) -> bool:
        """调用供应商前落一次「提交意图」，崩在中途时重启能识别出来。

        同时记下 `started_at`：巡检按「什么时候开始提交」判陈旧，
        才不会把排队很久、其实刚开始干活的任务当成孤儿（见 maintenance）。
        """
        job = await _guard(self.session, record_id)
        if job is None:
            return False
        job.submit_attempts += 1
        job.started_at = datetime.now(tz=UTC)
        return True

    async def clear_submit_attempt(self, record_id: str) -> bool:
        job = await _guard(self.session, record_id)
        if job is None:
            return False
        job.submit_attempts = 0
        return True

    @staticmethod
    def _snapshot_submit_mode(job: ImageJob) -> str | None:
        """提交成功时把供应商形态写进记录，恢复时不必再解释当前模型目录。

        取不到就留 NULL：恢复器对无快照的老记录按「不能续查」处理，
        宁可让人核对，也不猜一个可能把记录判死的答案。
        """
        from infra import get_model_router

        model = job.model_id or job.model
        try:
            router = get_model_router()
            if router.resolve(model) is None:
                return None
            provider = router.provider_for(model)
        except Exception:  # noqa: BLE001 - 快照是增强，不能让一次成功提交失败
            return None
        return "async" if getattr(provider, "supports_poll", False) else "sync"

    async def mark_submitted(self, record_id: str, external_id: str) -> bool:
        job = await _guard(self.session, record_id)
        if job is None:
            return False
        job.external_id = external_id
        job.status = STATUS_RUNNING
        job.started_at = datetime.now(tz=UTC)
        job.progress = max(job.progress, 1)
        # 提交意图已完成使命，必须复位：否则后续任何失败后的重试
        # 都会被误判成「提交结果未知」而拒绝重提
        job.submit_attempts = 0
        job.submit_mode = self._snapshot_submit_mode(job)
        return True

    async def mark_progress(self, record_id: str, progress: int) -> bool:
        job = await _guard(self.session, record_id)
        if job is None:
            return False
        job.progress = min(99, max(job.progress, progress))
        job.status = STATUS_RUNNING
        return True

    async def mark_succeeded(
        self,
        record_id: str,
        *,
        output_url: str | None,
        cover_url: str | None,
        progress: int = 100,
    ) -> bool:
        job = await _guard(self.session, record_id)
        if job is None:
            return False
        job.status = STATUS_SUCCEEDED
        job.progress = 100
        job.output_url = output_url
        # 图片的封面就是它自己；供应商没给 cover 时不要用旧的空串覆盖
        if cover_url:
            job.cover_url = cover_url
        job.error_message = None
        job.finished_at = datetime.now(tz=UTC)
        return True

    async def mark_failed(self, record_id: str, error: str) -> bool:
        job = await _guard(self.session, record_id)
        if job is None:
            return False
        job.status = STATUS_FAILED
        job.error_message = error
        job.finished_at = datetime.now(tz=UTC)
        return True


def _allowed_hosts(raw: str) -> frozenset[str]:
    return frozenset(item.strip().lower() for item in raw.split(",") if item.strip())


def _validate_asset_url(url: str, allowed: frozenset[str]) -> None:
    """下载前校验 URL；每一跳重定向都要重新过一遍这个函数。

    URL 完全来自供应商响应体，而 `AGNES_BASE_URL` 本身是运维可配的环境变量——
    配置指错、上游被劫持、响应被篡改都能把它变成 SSRF 入口（手册 §5.6③）。
    """
    parts = urlparse(url)
    if parts.scheme not in ("https", "http"):
        raise ValueError(f"成图地址协议不可用：{parts.scheme!r}")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("成图地址缺少 host")

    if allowed:
        if host not in allowed:
            raise ValueError(f"成图地址 host 不在白名单 IMAGE_ASSET_ALLOWED_HOSTS 里：{host}")
        return

    # 没配白名单时的兜底：只拒绝**非公网** IP 字面量（环回 / 内网 /
    # 链路本地 / 元数据地址）；公网 IP 字面量与任意公网域名仍会放行，
    # 且这里不解析 DNS，域名解析到内网时拦不住。生产必须配白名单。
    try:
        addr = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        addr = None
    if addr is not None and (
        addr.is_private or addr.is_loopback or addr.is_link_local or not addr.is_global
    ):
        raise ValueError(f"成图地址指向非公网 IP：{host}")
    if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_SUFFIXES):
        raise ValueError(f"成图地址指向内网域名：{host}")


def _has_magic(data: bytes, suffix: str) -> bool:
    """按文件魔数确认内容，别把一段 HTML / 内部文本当图片存进自己的盘。"""
    if suffix == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix == ".jpg":
        return data.startswith(b"\xff\xd8\xff")
    if suffix == ".webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def _with_suffix(name: str, stored: str) -> str:
    """给草稿名补上**文件本身**的后缀。

    界面上显示会剥掉后缀（前端 `displayName`），但库里存的名字要有：打包下载
    拿 `name` 当条目名，缺后缀的文件双击打不开。后缀只认 `stored` 的（那是真实
    产物的格式），用户给的名字里带什么后缀都不算数。
    """
    suffix = Path(stored.split("?", 1)[0]).suffix
    base = Path(name).stem if Path(name).suffix else name
    return f"{base}{suffix}" if suffix else base


@register_pipeline
class ImageGenerationPipeline:
    kind = KIND_IMAGE
    capability = Capability.IMAGE

    def store(self, session: AsyncSession) -> ImageJobStore:
        return ImageJobStore(session)

    async def list_unfinished(
        self, session: AsyncSession, limit: int
    ) -> Sequence[GenerationRecord]:
        """框架恢复器的可选钩子：SQL 仍由 Store 负责，pipeline 只做转发。"""
        return await self.store(session).list_unfinished(session, limit)

    async def build_request(self, record: GenerationRecord) -> GenerationRequest:
        return GenerationRequest(
            capability=self.capability,
            model=record.model,
            prompt=record.prompt,
            params=record.params,
            request_id=record.id,
        )

    async def archive_request(
        self, session: AsyncSession, record: GenerationRecord, stored: str
    ) -> IngestRequest:
        """把一条成功的生图记录翻译成「入草稿箱」的请求（可选钩子）。

        `stored` 是 `persist_output` 的产物：落盘成功时是对象 key，落盘失败时
        仍是供应商的签名地址。两种都照实转交——入库方按 `storageKey` 零拷贝、
        按 `sourceUrl` 现取现存，签名地址因此不会作为「库里的图」留在库里。

        名字取作业名（用户建任务时填的那个），不带参数名进快照：`record.params`
        是直接发给供应商的请求体，往里面塞 `name` 会变成供应商收到的多余字段。
        """
        job = await session.get(ImageJob, record.id)
        name = (job.name if job is not None else "") or f"生成图片-{record.id[:8]}"
        is_url = stored.startswith(("http://", "https://"))
        # 额外参数（quality / seed…）一并留档：资产详情里的「生成参数」要能还原
        # 当时到底怎么出的图，只留 size / count 的话复现不出来
        extra = {key: value for key, value in record.params.items() if key not in ("size", "count")}
        return IngestRequest(
            source_module=KIND_IMAGE,
            source_task_id=record.id,
            # 幂等键要带上**供应商任务号**，不能只用作业 id：
            # 同一个作业可以「重试」出另一张图（重试会清空 external_id 重投），
            # 只用 `image:{id}` 的话重试后新图会被幂等判成「已经入过库」，
            # 草稿箱里永远停着上一版的图。同一跳轮询重复回调时 external_id
            # 不变，仍然是同一行。
            source_key=f"{KIND_IMAGE}:{record.id}:{record.external_id or 'v0'}",
            kind=LibraryAssetKind.IMAGE,
            owner_id=record.owner_id,
            name=_with_suffix(name, stored),
            storage_key="" if is_url else stored.lstrip("/"),
            source_url=stored if is_url else "",
            generation={
                **extra,
                "model": record.model,
                "prompt": record.prompt,
                "size": job.size if job is not None else record.params.get("size"),
                "count": job.count if job is not None else record.params.get("count"),
            },
        )

    async def persist_output(self, record_id: str, url: str) -> str:
        """把成图落到自己的存储上，返回**对象 key**（不是 URL）。

        `generation.runner` 在落结果前会调这个可选的钩子；没有它，
        `output_url` 存的就是供应商签名地址——几小时后前端开始出现裂图，
        而失败表现是「历史页图片加载不出来」，离真正原因很远。

        返回值是 key 而不是 `url_for()` 的产物：落库地址一旦带上网址形态，
        换存储驱动（本地 → s3 → OSS）时存量链接会整批失联。地址在出参时按
        当前驱动现签，见 `infra.artifacts`。

        下载失败由框架兜底（保留原 URL 并记日志），因为**下载失败不该把一次
        成功的生成改成失败**。这里的 host 白名单 / 禁重定向 / 大小上限 / 魔数
        校验都是同一条原则：宁可退回原图，也不要把不可信的外部响应写进自己的盘。
        """
        if url.startswith("/"):
            return url  # 已经是自己的相对地址（重放/演示数据），不重复搬运
        # 其余一律交给 _validate_asset_url：ftp://、file:// 之类不能被当成
        # 「自己的地址」直接返回，那等于跳过协议校验。

        config = get_image_asset_settings()
        allowed = _allowed_hosts(config.allowed_hosts)
        current = url

        async with httpx.AsyncClient(
            timeout=config.timeout_sec, follow_redirects=False
        ) as client:
            for hop in range(_MAX_REDIRECTS + 1):
                _validate_asset_url(current, allowed)
                async with client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        if not location:
                            raise ValueError("成图下载地址返回了空的重定向目标")
                        # 每一跳都会重新过一遍 host 校验，不接受「初始可信 → 302 到内网」
                        current = urljoin(current, location)
                        continue

                    response.raise_for_status()
                    raw_type = (
                        response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    )
                    suffix = _ALLOWED_IMAGE_TYPES.get(raw_type)
                    if suffix is None:
                        raise ValueError(f"成图 content-type 不在白名单：{raw_type!r}")

                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes(chunk_size=_CHUNK):
                        total += len(chunk)
                        if total > config.max_bytes:
                            raise ValueError(
                                f"成图超过 IMAGE_ASSET_MAX_BYTES（{config.max_bytes}）"
                            )
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    if not data or not _has_magic(data, suffix):
                        raise ValueError("成图内容与其 content-type 的魔数不符")

                key = f"images/{record_id}{suffix}"
                await get_storage().put(key, data, raw_type)
                logger.info(
                    "成图已落盘 id=%s key=%s bytes=%d hops=%d", record_id, key, len(data), hop
                )
                return key

        raise ValueError(f"成图下载地址重定向超过 {_MAX_REDIRECTS} 跳")


__all__ = [
    "KIND_IMAGE",
    "ImageAssetSettings",
    "ImageGenerationPipeline",
    "ImageJobStore",
    "get_image_asset_settings",
]
