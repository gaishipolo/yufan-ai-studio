"""视频任务的生成流水线：把 `video_tasks` 表适配到通用生成框架。

这是**接入新生成能力的参考实现**，同事要做「图片批量生成」「配音」时，
照这 ~100 行复制改名即可，不需要理解框架内部。
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.exceptions import BadRequest
from core.library_text import inject_text_refs
from core.logging import get_logger
from core.naming import generated_file_name
from core.ports.library_ingest import IngestRequest
from core.ports.library_lookup import LibraryAssetKind, get_library_lookup
from db.guarded_update import update_unless_terminal
from db.session import get_session_factory
from generation.pipeline import register_pipeline
from generation.spec import GenerationRecord
from infra import get_model_router, get_storage
from infra.ai.base import Capability, GenerationRequest, PollCapable
from infra.archive import get_archive_storage
from infra.archive.runs import run_dir
from infra.executor_output import EXECUTOR_OUTPUT_RELATIVE, RUN_OUTPUT_URL
from infra.http import fetch_to_file, validate_public_url
from infra.media_channel import (
    MediaChannel,
    declared_channels,
    preferred_channels,
    record_channel_fallbacks,
    summarize_channels,
)
from infra.media_probe import MAX_BYTES
from infra.reference_inline import MIME_BY_SUFFIX, prepare_inline_payloads
from modules.task.enums import TaskStatus
from modules.task.models import VideoTask, parse_params_json

KIND_VIDEO = "video"

logger = get_logger(__name__)

# 执行器把成片下载到运行工作区的这个相对路径下（`runtime/vendor/.../canvas-run.mjs`
# 的 `downloadResult` 写死的名字）。驱动拿不到公网地址，只能回一个指向工作区的
# 相对地址，所以「搬进对象存储」这一步必须由这里按工作区路径取字节。
#
# 相对路径与「本地地址」的格式都住在 `infra/executor_output.py` —— 驱动
# （`bailian` / `libtv`）写、这里读，字面量只有一份。漂移的后果是「任务成功
# 但资产库空」：驱动写一个路径、框架按另一个路径找，找不到就当没有成片。
_EXECUTOR_OUTPUT = EXECUTOR_OUTPUT_RELATIVE
# 驱动对这类产物的约定形状；换执行器时改这一处。
_RUN_OUTPUT_URL = RUN_OUTPUT_URL

# 单份参考素材的字节上限（与生图链同一数值之外的**视频链**口径）：base64 会把它撑大 4/3。
#
# 取**所有供应商里最宽的一档**（Agnes 视频 15MB）。物化期只做「别把 worker 内存
# 打爆」这一层兜底，按供应商区分的单份 / 合计体积已经在受理期按
# `ReferenceLimit.max_bytes` / `max_total_bytes` 判过了；这里写窄了会反过来
# 把受理期刚放行的素材在物化期杀掉，用户看到的是「提交成功又立刻失败」。
_MAX_REFERENCE_BYTES = 15 * 1024 * 1024

# 这条链的**落地能力**（框架侧真有哪些通道的实现）：
#
# - BASE64：框架读字节 → 编码成 `data:` URI（Agnes 视频 / Grsai 视频走这条）；
# - OSS / 工作区：框架**不读**字节，素材留在归档侧，由物化器落工作区
#   （百炼自己上传 OSS、LibTV 读工作区文件）。
#
# 供应商声明了不在这个集合里的通道 → 直接报错：没有实现就不能兑现，
# 静默丢素材会让用户拿到「参考图没生效」的成片。
_SUPPORTED_CHANNELS = frozenset(
    {MediaChannel.BASE64, MediaChannel.OSS, MediaChannel.WORKSPACE}
)

# 任务表的终态用 completed/failed（前端契约），与框架默认的 succeeded 不同，
# 因此显式声明终态集合。
_TERMINAL = (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value)


class VideoTaskStore:
    """`video_tasks` ↔ `GenerationRecord` 的映射。不负责 commit，由框架统一提交。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _get(self, record_id: str) -> VideoTask | None:
        return await self.session.get(VideoTask, record_id)

    async def load(self, record_id: str) -> GenerationRecord | None:
        task = await self._get(record_id)
        if task is None:
            return None
        params: dict[str, object] = {
            "durationSec": task.duration_sec,
            "aspectRatio": task.aspect_ratio,
        }
        # 模型声明的其余参数（受理期按 schema 校验后落库的那一份）。
        # 必须带上：重试是从这一行重建生成请求的，不带就会用默认参数重新提交 ——
        # 用户选的 quality / seed 无声丢失，而页面上什么问题都看不出来。
        # 顶层那几个键不在这里（各自有列），避免两份真相。
        params.update(parse_params_json(task.params_json))
        # 计费模式要一路带到归档工作区的 `run.json`：桥接层 `bridge.mjs` 靠
        # 那个字段决定走免费队列还是积分加速。历史行没有这个值（迁移前建的），
        # 按 `free` 处理 —— 保守的那一侧，不会误扣积分。
        billing_mode = (task.billing_mode or "free").strip() or "free"
        params["billingMode"] = billing_mode
        if task.style_preset:
            params["stylePreset"] = task.style_preset
        # 把受理期校验过的资产库 id 换成驱动能直接取字节的存储键。物化器
        # （`modules/model_driver/generic_inputs.py`）只认这批 key，不查库、
        # 不认用户：权限与存在性在 POST /tasks 受理时已经判完。
        #
        # 只有「声明了能收参考素材」的模型才需要这一步：纯文生视频模型压根
        # 不会读 params.referenceKeys，为它们查库 + 严格校验只会让历史行
        # （删过的图、迁移前的假 id）在轮询时莫名失败。
        spec = get_model_router().spec_for(task.model_id or task.model)
        if task.reference_library_ids and spec and spec.max_references > 0:
            found = await get_library_lookup().get_many(
                self.session, task.reference_library_ids
            )
            # 删除保护（`modules/task/library_references.py`）保证任务行还在时
            # 资产不会被 purge；万一有历史脏行或运维直改，这里必须**整条拒绝**，
            # 不能像列表推导那样「查不到就悄悄跳过一张」——那会生成一段与提示词
            # 里「第二张图」对不上的成片，而且没有任何报错。
            lost = [
                asset_id
                for asset_id in task.reference_library_ids
                if asset_id not in found or not found[asset_id].storage_key
            ]
            if lost:
                raise BadRequest(f"参考素材已不可用，无法重建生成请求：{lost}")
            params["referenceKeys"] = [
                {
                    "id": asset_id,
                    "key": found[asset_id].storage_key,
                    "kind": found[asset_id].kind.value,
                    "name": found[asset_id].name,
                }
                for asset_id in task.reference_library_ids
            ]
        return GenerationRecord(
            # 参考素材的**存储 key**（不是地址）：链路层按供应商的声明决定
            # 走「内联 data URI」还是「落盘物化」那两条路（见 build_request）。
            reference_urls=tuple(
                found[asset_id].storage_key
                for asset_id in (task.reference_library_ids or [])
                if asset_id in found
            ),
            id=task.id,
            kind=KIND_VIDEO,
            # 路由供应商用目录 id；历史数据可能只有可读名（ModelRouter 两种都认）
            model=task.model_id or task.model,
            # 与生图同口径：把 `{{Text N}}` 展开成受理期冻结的正文，
            # 行上的 `prompt` 仍保留占位符供历史页原样展示。
            prompt=inject_text_refs(task.prompt, task.text_refs),
            status=task.status,
            owner_id=task.author_id,
            params=params,
            external_id=task.external_id,
            progress=task.progress,
            output_url=task.output_url,
            cover_url=task.cover_url or None,
            error=task.error_message,
            created_at=task.created_at,
            # 墙钟预算的起点必须传**本轮**开始时间，不能让它回落到 `created_at`。
            # `started_at` 由 `mark_submitted` 每轮写成本轮时间，而 `created_at`
            # 永远不会变：一个已经跑满 30 分钟被判超时的任务，重试后
            # `now - created_at` 立刻又大于预算，第一跳轮询当场再判一次超时 ——
            # 用户看到的就是「点重试，瞬间还是同一句生成超时」。
            # 与 `model_driver/store.py` 同口径（那边传的是 `job.started_at`）。
            timeout_started_at=task.started_at,
            submit_attempts=task.submit_attempts,
            submit_mode=task.submit_mode,
            terminal_statuses=_TERMINAL,
        )

    @staticmethod
    def _to_recovery_record(task: VideoTask) -> GenerationRecord:
        """恢复只需要决策字段；不在这里物化参考素材（那是轮询/提交的事）。"""
        return GenerationRecord(
            id=task.id,
            kind=KIND_VIDEO,
            model=task.model_id or task.model,
            prompt=task.prompt,
            status=task.status,
            owner_id=task.author_id,
            external_id=task.external_id,
            progress=task.progress,
            error=task.error_message,
            created_at=task.created_at,
            timeout_started_at=task.started_at,
            submit_attempts=task.submit_attempts,
            submit_mode=task.submit_mode,
            terminal_statuses=_TERMINAL,
        )

    async def list_unfinished(
        self,
        session: AsyncSession,
        limit: int | None = None,
        *,
        stale_after_sec: int | None = None,
    ) -> Sequence[GenerationRecord]:
        stmt = select(VideoTask).where(
            VideoTask.status.in_((TaskStatus.QUEUED.value, TaskStatus.RUNNING.value))
        )
        if stale_after_sec is not None:
            cutoff = datetime.now(tz=UTC) - timedelta(seconds=max(0, stale_after_sec))
            stmt = stmt.where(VideoTask.updated_at < cutoff)
        stmt = stmt.order_by(VideoTask.updated_at, VideoTask.id)
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = (await session.scalars(stmt)).all()
        return [self._to_recovery_record(row) for row in rows]

    async def mark_submitting(self, record_id: str) -> bool:
        """调用供应商前先落一次「提交意图」，崩在中途时重启能识别出来。"""
        return await update_unless_terminal(
            self.session,
            VideoTask,
            record_id,
            _TERMINAL,
            {
                "submit_attempts": VideoTask.submit_attempts + 1,
                "started_at": datetime.now(tz=UTC),
            },
        )

    async def clear_submit_attempt(self, record_id: str) -> bool:
        return await update_unless_terminal(
            self.session,
            VideoTask,
            record_id,
            _TERMINAL,
            {"submit_attempts": 0},
        )

    @staticmethod
    def _snapshot_submit_mode(task: VideoTask) -> str | None:
        """提交成功时冻结供应商形态；取不到就留 NULL，恢复按安全侧处理。"""
        model = task.model_id or task.model
        try:
            router = get_model_router()
            if router.resolve(model) is None:
                return None
            provider = router.provider_for(model)
        except Exception:  # noqa: BLE001 - 快照是增强，不能让成功提交失败
            # 恢复时按最保守的同步形态处理；但异常本身要留痕，否则「提交成功、
            # 恢复策略却悄悄降级」没有任何线索。
            logger.warning(
                "冻结提交形态失败，恢复按安全侧处理 model=%s",
                model,
                exc_info=True,
            )
            return None
        return "async" if isinstance(provider, PollCapable) and provider.supports_poll else "sync"

    async def mark_submitted(self, record_id: str, external_id: str) -> bool:
        task = await self._get(record_id)
        if task is None:
            return False
        progress = case(
            (VideoTask.progress < 1, 1),
            else_=VideoTask.progress,
        )
        return await update_unless_terminal(
            self.session,
            VideoTask,
            record_id,
            _TERMINAL,
            {
                "external_id": external_id,
                "status": TaskStatus.RUNNING.value,
                "started_at": datetime.now(tz=UTC),
                "progress": progress,
                # 提交意图已完成使命：不复位的话，「attempts>0」会一直残留，
                # 此后任何一次失败后的重试都会被误判为「提交结果未知」（R3-01）
                "submit_attempts": 0,
                "submit_mode": self._snapshot_submit_mode(task),
            },
        )

    async def reset_for_resubmit(self, record_id: str) -> bool:
        """重派前复位展示现场（旧 worker 的启动恢复口径）。"""
        return await update_unless_terminal(
            self.session,
            VideoTask,
            record_id,
            _TERMINAL,
            {
                "status": TaskStatus.QUEUED.value,
                "progress": 0,
                "error_message": None,
                "started_at": None,
            },
        )

    async def set_external_id(self, record_id: str, external_id: str) -> bool:
        """从提交台账回填凭证：只写 external_id + running，不复位提交计数。"""
        return await update_unless_terminal(
            self.session,
            VideoTask,
            record_id,
            _TERMINAL,
            {
                "external_id": external_id,
                "status": TaskStatus.RUNNING.value,
                # 台账能给出凭证就说明这次提交有独立任务号 = 异步形态；
                # 已有快照不动，它是「提交当时」的事实。
                "submit_mode": case(
                    (VideoTask.submit_mode.is_(None), "async"),
                    else_=VideoTask.submit_mode,
                ),
            },
        )

    async def mark_progress(self, record_id: str, progress: int) -> bool:
        # 进度只增不减，且不提前到达 100（终态才置 100）
        next_progress = case(
            (VideoTask.progress < progress, progress),
            else_=VideoTask.progress,
        )
        return await update_unless_terminal(
            self.session,
            VideoTask,
            record_id,
            _TERMINAL,
            {
                "progress": case((next_progress > 99, 99), else_=next_progress),
                "status": TaskStatus.RUNNING.value,
            },
        )

    async def extend_deadline(self, record_id: str) -> bool:
        """上游还在排队：把墙钟起点挪到现在（`started_at` 就是 `_timed_out` 的锚点）。

        只动起点，不动 `created_at`：真正的硬上限按建单时间算，挪起点不能绕过它。
        """
        return await update_unless_terminal(
            self.session,
            VideoTask,
            record_id,
            _TERMINAL,
            {"started_at": datetime.now(tz=UTC)},
        )

    async def list_claimed_external_ids(self) -> set[str]:
        rows = (
            await self.session.scalars(
                select(VideoTask.external_id).where(VideoTask.external_id.is_not(None))
            )
        ).all()
        return {ext for ext in rows if ext}

    async def mark_succeeded(
        self,
        record_id: str,
        *,
        output_url: str | None,
        cover_url: str | None,
        progress: int = 100,
    ) -> bool:
        values: dict[str, object] = {
            "status": TaskStatus.COMPLETED.value,
            "progress": 100,
            "downloadable": True,
            "output_url": output_url,
            "error_message": None,
            "finished_at": datetime.now(tz=UTC),
        }
        if cover_url:
            values["cover_url"] = cover_url
        return await update_unless_terminal(
            self.session, VideoTask, record_id, _TERMINAL, values
        )

    async def mark_failed(self, record_id: str, error: str) -> bool:
        return await update_unless_terminal(
            self.session,
            VideoTask,
            record_id,
            _TERMINAL,
            {
                "status": TaskStatus.FAILED.value,
                "error_message": error,
                "finished_at": datetime.now(tz=UTC),
            },
        )


@register_pipeline
class VideoGenerationPipeline:
    kind = KIND_VIDEO
    capability = Capability.VIDEO

    # 与生图同口径：历史记录按当次那条记录的模型找回原供应商。视频单条
    # 成本更高，兜底换渠道的后果比生图更贵（时长、分辨率、计费口径全不同）。
    strict_model = True

    # 模型从目录里消失 = 这条记录不可能再有结果 → 当场判失败。
    fail_on_model_gone = True

    def store(self, session: AsyncSession) -> VideoTaskStore:
        return VideoTaskStore(session)

    async def list_unfinished(
        self,
        session: AsyncSession,
        limit: int | None = None,
        *,
        stale_after_sec: int | None = None,
    ) -> Sequence[GenerationRecord]:
        return await self.store(session).list_unfinished(
            session, limit, stale_after_sec=stale_after_sec
        )

    async def build_request(self, record: GenerationRecord) -> GenerationRequest:
        # 参考素材走哪条通道，由**供应商自己**声明（`infra/media_channel.py`）：
        #
        # - 内联（Agnes 视频 / Grsai 视频）：上游只收公网地址或 data URI，而我们的
        #   存储在内网、给不出公网地址，于是把字节读成 data URI 直接进请求体；
        # - 落盘物化（LibTV / 百炼）：`params["referenceKeys"]` 交给物化器写进
        #   归档工作区，上传时才有地址（那条老路一个字都不动）。
        #
        # 以前这里是「声明了 `inline_references` 就全内联、否则全走老路」一个开关，
        # 分不了素材类型；现在按类型分别选路，通道的落地方式还是那两条。
        provider = get_model_router().provider_for(record.model)
        references, plans = await prepare_inline_payloads(
            record.reference_urls,
            accepted=declared_channels(provider),
            preferred=preferred_channels(provider),
            supported=_SUPPORTED_CHANNELS,
            mime_by_suffix=MIME_BY_SUFFIX,
            max_bytes=_MAX_REFERENCE_BYTES,
        )
        await record_channel_fallbacks(plans, target_id=record.id, actor_id=record.owner_id)
        return GenerationRequest(
            capability=self.capability,
            model=record.model,
            prompt=record.prompt,
            params=record.params,
            reference_urls=references,
            request_id=record.id,
            channel_used=summarize_channels(plans),
        )

    async def persist_output(self, record_id: str, url: str) -> str:
        """把成片搬进对象存储，返回**对象 key**（可选钩子）。

        本地执行器型供应商（LibTV）不提供公网地址：执行器把成片下载到运行工作区，
        驱动只能回一个指向工作区的相对地址。这类地址有两种死法，都需要在这里拦住：

        1. 落库后前端拿它播放/下载 —— 地址指向的路由随 P3 删除 `modules/template`
           一起没了，返回 404；
        2. 入库时它既不是 `http(s)` 也没有对象，被当成对象 key 去存储里找 →
           `GenerationSourceInvalid`，于是「任务成功、草稿箱空」。

        搬完之后与其它供应商同一套口径：库里存 key、出参现签、入库零拷贝。
        给的是 `https` 直链的供应商（Agnes）原样返回——它们的地址长期有效，
        多搬一份只是浪费存储。

        取不到字节就抛：框架会记一条日志并退回原地址，不会把「成功」改成「失败」。
        """
        match = _RUN_OUTPUT_URL.search(url)
        if match is None:
            if not url.startswith(("http://", "https://")):
                return url
            # 去字幕结果返回的是上游临时 CDN 地址（`item.result_url`），必须搬进
            # 平台对象存储，避免外部链接过期或生产环境 `LIBRARY_INGEST_ALLOWED_HOSTS` 拦下入库；
            # 其它模型的 `http(s)` 直链（Agnes）维持原样返回。
            async with get_session_factory()() as session:
                task = await session.get(VideoTask, record_id)
            if task is None or task.variant != "subtitle_erased":
                return url
            raw_suffix = Path(url.split("?", 1)[0]).suffix.lower()
            suffix = raw_suffix if raw_suffix in {".mp4", ".mov", ".webm"} else ".mp4"
            content_type = {
                ".mp4": "video/mp4",
                ".mov": "video/quicktime",
                ".webm": "video/webm",
            }[suffix]
            key = f"videos/{record_id}{suffix}"
            with tempfile.TemporaryDirectory(prefix="task-persist-") as folder:
                target = Path(folder) / f"output{suffix}"
                await fetch_to_file(
                    url,
                    target,
                    max_bytes=MAX_BYTES,
                    retries=2,
                    timeout=settings.generation_persist_timeout_sec,
                    guard=validate_public_url,
                )
                await get_storage().put_file(key, target, content_type)
            return key

        # `run_dir` 内部要读指针 JSON（`storage.exists` / `storage.read`），
        # 之后还要 `is_file()` —— 而 `ArchiveStorage` 的**全部方法都是同步的**
        # （S3 归档下是 boto3，SMB 下是一次网络往返，重试退避里还有 `time.sleep`）。
        # 直接在协程里调，会把整个事件循环按住那么久：这段时间里所有 HTTP 请求、
        # 队列消费、其它周期任务一起停摆，而日志里一条异常都没有。
        #
        # 一次 `to_thread` 把「定位 + 存在性判断」都做完，省一次跨线程往返。
        def _locate() -> tuple[Path, bool]:
            path = run_dir(get_archive_storage(), match.group(1)) / _EXECUTOR_OUTPUT
            return path, path.is_file()

        source, exists = await asyncio.to_thread(_locate)
        if not exists:
            raise FileNotFoundError(f"归档工作区里没有成片：{source}")
        key = f"videos/{record_id}{Path(_EXECUTOR_OUTPUT).suffix}"
        await get_storage().put_file(key, source, "video/mp4")
        return key

    async def archive_request(
        self, session: AsyncSession, record: GenerationRecord, stored: str
    ) -> IngestRequest:
        """把一条成功的视频任务翻译成「入草稿箱」的请求（可选钩子）。

        没有这个方法时 `generation.runner._archive()` 会在 `getattr` 那一步
        静默返回——用户视频生成完了，资产库里一条都没有（2026-11-11 实测：
        图片来源入库 6 条、视频 0 条，日志里「生成结果已入草稿箱」出现 0 次）。

        `stored` 是 `persist_output` 的产物，有两种形态：搬过的是对象 key
        （本地执行器型供应商，见上面的 `persist_output`）；没搬的仍是供应商
        直链（Agnes 的 `cos-platform-outputs.../xxx.mp4`，长期有效、不带签名）。
        两种都交给入库方：key 走零拷贝，直链走 `sourceUrl` 现取现存——字节最终
        落在资产库自己的存储里，供应商链接以后失效也不影响库里的那一份。
        """
        task = await session.get(VideoTask, record.id)
        # 资产库里的名字用「作者-生成时间」而不是标题：标题是一句描述，同一句话
        # 可以生成十次；用户下载后要能分清哪个是新的（见 `core.naming`）。
        name = generated_file_name(
            task.author_name if task is not None else "",
            task.finished_at or task.created_at if task is not None else None,
        )
        is_url = stored.startswith(("http://", "https://"))
        # 去字幕版：把**原片那条资产**原地换成新内容，资产库只留一版。
        # 原片在任务里仍可回看（`video_tasks.output_url` 是任务自己的产物，
        # 与资产库里的那一行是两回事）。
        replaces = ""
        if task is not None and task.variant == "subtitle_erased" and task.derived_from_task_id:
            parent = await session.get(VideoTask, task.derived_from_task_id)
            replaces = (parent.library_asset_id or "") if parent is not None else ""
            # `generated_file_name` 不带扩展名（后缀由 `_with_suffix` 补），
            # 所以不能按 `.mp4` 判断，否则标记永远加不上。
            stem = Path(name).stem if Path(name).suffix else name
            name = f"{stem}-去字幕"

        return IngestRequest(
            source_module=KIND_VIDEO,
            source_task_id=record.id,
            replaces_asset_id=replaces,
            # 幂等键必须带**供应商任务号**：`POST /tasks/{id}/retry` 会清空
            # `external_id` 重投（见 `service.retry_task`），只用 `video:{id}`
            # 的话重试后新出的片子会被幂等判成「已入过库」，草稿箱里永远停在
            # 上一版。同一跳轮询重复回调时 external_id 不变，仍是同一行。
            source_key=f"{KIND_VIDEO}:{record.id}:{record.external_id or 'v0'}",
            kind=LibraryAssetKind.VIDEO,
            owner_id=record.owner_id,
            name=_with_suffix(name, stored),
            storage_key="" if is_url else stored.lstrip("/"),
            source_url=stored if is_url else "",
            generation=_snapshot(task, record),
        )


    async def attach_asset(self, session: AsyncSession, record_id: str, asset_id: str) -> bool:
        """把入库得到的资产 id 记在任务上（派生任务靠它找到「原片资产」去替换）。"""
        task = await session.get(VideoTask, record_id)
        if task is None:
            return False
        task.library_asset_id = asset_id
        return True

    async def attach_cover(
        self, session: AsyncSession, record_id: str, cover_key: str
    ) -> bool:
        """把资产库派生好的首帧写回任务封面（可选钩子）。

        Agnes 只回 mp4 直链、**不给首帧**，所以卡片封面只能来自我们自己抽的那张：
        入库时 `library` 用 ffmpeg 取第一帧并登记 `cover_key`，框架在入库成功之后
        把 key 交回这里。写 key 不写地址——`feed` 会按当前存储驱动现签，
        换驱动不会整屏裂图。
        """
        task = await session.get(VideoTask, record_id)
        if task is None:
            return False
        task.cover_url = cover_key
        task.updated_at = datetime.now(tz=UTC)
        return True


def _with_suffix(name: str, stored: str) -> str:
    """给条目名补上真实产物的后缀（`mp4`）。

    用户填的是标题（「一只狸花猫在草地上奔跑」），没有后缀；资产库里存成
    无后缀的名字，下载下来双击打不开。后缀只认 `stored` 的——那是真实产物
    的格式，标题里自带什么后缀都不算数。
    """
    suffix = Path(stored.split("?", 1)[0]).suffix
    base = Path(name).stem if Path(name).suffix else name
    return f"{base}{suffix}" if suffix else base


def _snapshot(task: VideoTask | None, record: GenerationRecord) -> dict[str, object]:
    """入库快照。键名必须是资产库 `_SNAPSHOT_FIELDS` 里认得的（snake_case），
    认不出的键会被静默丢弃——写成 `durationSec` 等于没记。
    """
    if task is None:
        return {"model": record.model, "prompt": record.prompt}
    return {
        "model": task.model,
        "model_id": task.model_id or record.model,
        # 存**注入正文之后**的提示词：资产库详情是给人看的，`{{Text 1}}` 在那里
        # 没有上下文可解析（快照不带文案 id）。与生图快照同口径。
        # `{{Mixed N}}` 保持原样 —— 资产库有 `reference_library_ids`，前端能还原缩略图。
        "prompt": inject_text_refs(task.prompt, task.text_refs),
        "duration_sec": float(task.duration_sec),
        "aspect_ratio": task.aspect_ratio,
        "style_preset": task.style_preset or "",
        # v2 口径：这列存的是资产库 id。以前写成 `reference_asset_ids`（v1 列名），
        # 等于把 v2 id 混进「旧素材」那一列，档案里那行参考图指向另一套 id 空间。
        "reference_library_ids": list(task.reference_library_ids or []),
        "external_id": task.external_id or "",
        "generated_at": task.finished_at,
    }


__all__ = ["KIND_VIDEO", "VideoGenerationPipeline", "VideoTaskStore"]
