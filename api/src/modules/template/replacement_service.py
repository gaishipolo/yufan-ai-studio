"""替换运行：准备输入、提交生成、评价、回收站。

数据从「`run.json` + 一堆旁挂 JSON」搬进 `replacement_runs` 之后，有三处判断被
重新落位，都是有意为之：

1. **owner 过滤下沉到 SQL**。旧 `list_runs()` 要把所有档案读出来再在 Python 里
   比 `owner_id`，注释里写着「留待单独一轮」——本轮就是那一轮。
2. **幂等从指纹文件变成主键**。`id` 就是前端传的 `request_id`，重复提交命中
   同一行；指纹只用来区分「同一 id 但配置变了」。
3. **最终授权校验前移到业务层**。执行器（`libtv_process.run_node`）只有文件
   系统、查不了库，现在由这里在排队前校验「原片还可用、封装还已发布」。

**状态机的语义没变**，尤其 `submission_unknown`：它表示「上一次提交可能已经
花了钱」，既不能当失败重提，也不能当成功。唯一出口是 `reconfirm()`。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from core import audit
from core.exceptions import BadRequest, Conflict, Forbidden, NotFound, UpstreamError
from core.feed import ReplacementFeedRecord
from core.logging import get_logger
from core.media import StoredMedia
from core.ports.media import get_asset_lookup
from core.serving import Served
from infra.ai.libtv import get_libtv_settings
from infra.archive import ArchiveStorage, get_archive_storage
from modules.template import video_template_store
from modules.template.authorization import authorization_gap
from modules.template.models import ReplacementReview as ReviewRow
from modules.template.models import ReplacementRun
from modules.template.pack_roles import PACK_ROLES, PACK_SCOPES, usage_for
from modules.template.repository import PackRepository, RunRepository
from modules.template.run_clock import elapsed_seconds
from modules.template.schema import (
    PackBinding,
    PrepareReplacement,
    ReplacementCatalog,
    ReplacementInputAsset,
    ReplacementInputSummary,
    ReplacementReview,
    ReplacementReviewEvent,
    ReplacementReviewIn,
    ReplacementRunOut,
)
from modules.template.titles import task_name
from modules.template.video_template_options import scope_options, validate_options
from modules.template.workspace import (
    BRIDGE,
    RUNTIME,
    Compilation,
    compile_prompt,
    create_workspace,
    ensure_layout,
    run_path,
)

if TYPE_CHECKING:
    from modules.template.video_template_schema import VideoTemplateOut

logger = get_logger(__name__)

# 参考图在提示词里的角色名。封装里的 `kind` 决定它替换的是什么，
# 这张表把「业务分类」翻译成「编译器认识的角色」。
_REFERENCE_ROLE = {"character": "identity", "product": "target_product", "scene": "target_scene"}

# 非终态：启动恢复要扫这些行（状态机见 models.RUN_STATUSES 的注释）
UNFINISHED = ("preparing", "prepared", "queued", "waiting_free", "waiting_executor", "running")

# 评价可以反复改：只留最近 N 次结论，别把这一行写成一本日志（旧实现同样截 20 条）
REVIEW_HISTORY_LIMIT = 20


class ReplacementService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.runs = RunRepository(session)
        self.packs = PackRepository(session)
        self.storage: ArchiveStorage = get_archive_storage()

    # ------------------------------------------------------------------ #
    # 读
    # ------------------------------------------------------------------ #
    async def catalog(self, owner: str) -> ReplacementCatalog:
        from modules.template.pack_service import PackService

        runs = await self.list_runs(owner)
        packs = await PackService(self.session).available()
        return ReplacementCatalog(runs=runs, packs=packs)

    async def list_runs(self, owner: str) -> list[ReplacementRunOut]:
        """列表**包含**回收站里的记录：前端「回收站」页签读的就是这一份。

        `deleted_at` 由出参带出去，前端按它分页签。过滤掉的话回收站永远是空的，
        而恢复按钮也就没有入口——`list_owned(include_deleted=False)` 是任务中心的
        口径（那里删掉的记录不该再出现），不是这里的。
        """
        rows = await self.runs.list_owned(owner, include_deleted=True)
        reviews = await self._review_map([row.id for row in rows])
        return [self._run_out(row, reviews.get(row.id)) for row in rows]

    async def detail(self, run_id: str, owner: str) -> ReplacementRunOut:
        row = await self.runs.get_or_404(run_id, owner=owner)
        return self._run_out(row, await self.runs.review(row.id))

    async def feed_records(self, owner: str) -> list[ReplacementFeedRecord]:
        """任务中心只读镜像：不读提示词与完整输入摘要，只取列表要的字段。

        **不含回收站里的记录**：任务中心是「正在发生什么」，删除是用户主动
        把它从这里收走，再出现就白删了。
        """
        rows = await self.runs.list_owned(owner)
        records: list[ReplacementFeedRecord] = []
        for row in rows:
            summary = row.input_summary or {}
            assets = summary.get("assets") or []
            source_name = next(
                (item.get("name") for item in assets if item.get("role") == "source_video"),
                None,
            )
            records.append(
                ReplacementFeedRecord(
                    id=row.id,
                    kind=row.kind,
                    target=row.target,
                    status=row.status,
                    created_at=row.created_at,
                    title=_display_title(row, source_name),
                    source_name=source_name or "原片已不在",
                    progress=row.progress_percent,
                    task_id=row.task_id,
                    output_url=self._feed_output_url(row),
                    last_error=row.last_error,
                    deleted_at=row.deleted_at,
                )
            )
        return records

    def _feed_output_url(self, run: ReplacementRun) -> str | None:
        """任务中心的成片地址，判据与 `output()` 一致。

        只认 `output_key` 的话，老档案（成片还在工作区、只是没上传归档存储）
        在列表里会被标成「没有结果」，点开却能下载——同一条记录两个答案。
        这里按 `output_key` → 工作区文件的顺序判断，与下载接口同源。
        """
        if self._output_available(run):
            return f"/api/v1/templates/replacement-runs/{run.id}/output"
        return None

    # ------------------------------------------------------------------ #
    # 准备
    # ------------------------------------------------------------------ #
    async def prepare(
        self, payload: PrepareReplacement, owner: str, *, can_read_assets: bool = True
    ) -> ReplacementRunOut:
        if not can_read_assets:
            raise Forbidden("需要素材读取权限")

        # 必须带已发布版本：散图路径在 HTTP 入口一律拒绝，脚本/测试用
        # `require_pack=False` 的旧入口已随重建移除（见文档的「刻意保留的差异」）。
        if not payload.pack_id or not payload.pack_version:
            raise BadRequest("必须选择专员发布的对象与版本，不能直接使用散图")
        frozen = await self.packs.get_version(payload.pack_id, payload.pack_version)
        pack = await self.packs.get_or_404(payload.pack_id)
        if frozen is None or pack.status != "published":
            raise BadRequest("素材包未发布或已停用，请重新选择")
        if pack.kind != payload.kind:
            raise BadRequest("素材包不存在或类型不匹配")

        payload = payload.model_copy(
            update={
                "target": pack.name,
                "reference_ids": list(frozen.reference_ids),
                "wardrobe_mode": frozen.wardrobe_mode,  # type: ignore[arg-type]
            }
        )

        # 视频模板按**原片**套用：工作台里换产品包，模板照样生效 —— 提示词不动，
        # 图片按「用途」（主图/三视图/细节）从当前素材包里取。没有模板的普通原片
        # （例如 0507 高端姐）走原来的通用提示词，行为一个字都不变。
        # 读模板目录是同步文件 IO，进线程池：prepare 是高频入口，不能卡事件循环。
        video_template = await asyncio.to_thread(
            video_template_store.find_for_run,
            payload.source_id,
            payload.pack_id,
            payload.kind,
            payload.video_template_id,
        )
        _check_scope(frozen.supported_scopes, payload, video_template)
        roles = {row.asset_id: row.role for row in await self.packs.references(pack.id)}
        if video_template is not None:
            _check_video_template(video_template, roles, pack.kind)

        source, refs = await self._validate_inputs(
            payload, owner, pack.owner_id, scope_required=video_template is None
        )
        run = await self.runs.get(payload.request_id.hex)
        fingerprint = _fingerprint(owner, payload)
        if run is not None:
            if run.fingerprint != fingerprint:
                raise Conflict("请求标识已被不同配置使用，请重新准备")
            if run.status not in ("preparing", "prepare_failed"):
                return self._run_out(run, await self.runs.review(run.id))
            run.status = "preparing"
            run.last_error = None
            run.error_code = None
        else:
            run = ReplacementRun(
                id=payload.request_id.hex,
                kind=payload.kind,
                target=payload.target,
                owner_id=owner,
                status="preparing",
                fingerprint=fingerprint,
                pack_id=payload.pack_id,
                pack_version=payload.pack_version,
                configuration=payload.model_dump(mode="json"),
            )
            await self.runs.add(run)
        await self.session.commit()

        # 目录 + `run.json` + 索引指针一起建：`run_path()` 认指针，
        # 少一样后面每个接口（详情、执行、取件）都会 404。
        path = await asyncio.to_thread(
            create_workspace,
            run.id,
            {
                "id": run.id,
                "status": run.status,
                "kind": run.kind,
                "target": run.target,
                "owner_id": run.owner_id,
                "billing_mode": run.billing_mode,
                "submit_attempts": run.submit_attempts,
            },
        )
        try:
            # 文件 IO 与 subprocess 放线程池：它们会阻塞几十毫秒到几秒，
            # 在事件循环里跑会把同进程的其他请求一起卡住。
            spec, compiled, summary = await asyncio.to_thread(
                _materialize_inputs,
                self.storage,
                path,
                payload,
                source,
                refs,
                frozen,
                video_template,
                roles,
            )
        except Exception:
            logger.exception("替换输入准备失败 run_id=%s", run.id)
            run = await self.runs.get_or_404(run.id)
            run.status = "prepare_failed"
            run.error_code = "PREPARATION_FAILED"
            run.last_error = "输入准备未完成，请检查素材或编译器后用同一配置继续"
            await self.runs.save(run)
            await self.session.commit()
            raise

        run = await self.runs.get_or_404(run.id)
        run.status = "prepared"
        run.fingerprint = fingerprint
        run.prompt = compiled.prompt
        run.prompt_spec = spec
        # `spec["duration"]` 是 `_materialize_inputs` 刚算出来的 int（1..15），
        # 不是外部输入；这里过 `isinstance` 只为把 `dict[str, object]` 收窄成 int，
        # 不引入新的失败分支。
        raw_duration = spec.get("duration")
        run.duration = raw_duration if isinstance(raw_duration, int) else 0
        run.input_summary = summary.model_dump(mode="json")
        run.last_error = None
        run.error_code = None
        await self.runs.replace_inputs(
            run.id,
            [
                (
                    item.id,
                    "source_video" if item is source else _REFERENCE_ROLE[payload.kind],
                    position,
                    "",
                    "",
                )
                for position, item in enumerate(
                    _media_in_mixed_order(video_template, source, refs, roles)
                )
            ],
        )
        await self.runs.save(run)
        await self.session.commit()
        # 投影一次：`prepare` 只归档不提交，但执行器（以及手工排查的人）读的是
        # `run.json`，它必须反映「已准备好、尚未提交」。
        from modules.template import worker

        await asyncio.to_thread(worker.sync_projection, run)
        return self._run_out(run, await self.runs.review(run.id))

    # ------------------------------------------------------------------ #
    # 执行
    # ------------------------------------------------------------------ #
    async def execute(
        self, run_id: str, owner: str, billing_mode: str | None = None
    ) -> ReplacementRunOut:
        from modules.template import worker

        current = await self.detail(run_id, owner)
        if current.deleted_at:
            raise Conflict("请先从回收站恢复任务，再继续执行")
        await self._assert_still_authorized(run_id, owner)
        # 先落计费模式再排队：执行器读的是行上的 billing_mode，
        # 反过来的话这一轮准备仍会按免费队列估价，加速就白点了。
        if billing_mode:
            await worker.set_billing_mode(self.session, run_id, owner, billing_mode)
        await worker.schedule(self.session, run_id, owner)
        return await self.detail(run_id, owner)

    async def reconfirm(
        self, run_id: str, owner: str, *, confirm_charged: bool
    ) -> ReplacementRunOut:
        """确认「上一次提交可能已经花了钱」后放行重提。

        没有这个出口时 `submission_unknown` 是永久死锁：排队拦、提交直接 return、
        恢复定格，三处都把路堵死。**先写审计再复位**——这一步本身就是
        「有人拍板再花一次钱」，留痕比成功更重要。
        """
        from modules.template import worker

        run, cleared = await worker.reconfirm_state(
            self.session, run_id, owner, confirm_charged=confirm_charged
        )
        await audit.record(
            "template.reconfirm",
            actor_id=owner,
            target_type="replacement_run",
            target_id=run_id,
            detail={
                "confirmedCharged": confirm_charged,
                "kind": run.kind,
                "target": run.target,
                "clearedSubmitAttempts": cleared,
            },
        )
        await self.session.commit()
        await worker.schedule(self.session, run_id, owner)
        return await self.detail(run_id, owner)

    async def set_deleted(self, run_id: str, owner: str, deleted: bool) -> ReplacementRunOut:
        """进/出回收站。**幂等**：已经是目标状态时什么都不写。

        每次都刷新 `deleted_at` 看着无害，实际有两个后果：回收站按删除时间排序，
        双击一次就跳到最前；前端拿到的 `deletedAt` 每次都不一样，重发请求会被
        当成「又删了一次」。
        """
        run = await self.runs.get_or_404(run_id, owner=owner)
        if deleted and run.deleted_at is None:
            run.deleted_at = datetime.now(UTC)
        elif not deleted and run.deleted_at is not None:
            run.deleted_at = None
        else:
            return self._run_out(run, await self.runs.review(run.id))
        await self.runs.save(run)
        await self.session.commit()
        return await self.detail(run_id, owner)

    async def review(
        self, run_id: str, payload: ReplacementReviewIn, owner: str
    ) -> ReplacementRunOut:
        run = await self.runs.get_or_404(run_id, owner=owner)
        if run.status not in ("success", "failed"):
            raise Conflict("成片尚未完成，暂不能评价")
        if run.status == "success" and not self._output_available(run):
            # 成片在评价之前被清理掉：没有可比对的画面，收下的结论只是记账。
            raise Conflict("成片尚未保存，暂不能评价")
        row = await self.runs.review(run.id)
        if row is None:
            row = ReviewRow(run_id=run.id, status="pending", version=0)
        if row.version != payload.expected_version:
            raise Conflict("评价已变化，请刷新后重试")

        event = ReplacementReviewEvent(
            status=payload.status,
            version=row.version + 1,
            reason_codes=payload.reason_codes,
            comment=payload.comment,
            reviewed_by=owner,
            reviewed_at=datetime.now(UTC),
        )
        # history 只放**这一条之前**的结论：出参里 `status` 已经是当前值，
        # 再把当前塞进 history，前端时间线最后一条会重复，版本号也对不上。
        # 第一版之前是「还没有人评价」的 pending（version 0），照样记一笔——
        # 前端按 history 的长度画修订序号，缺了它序号会从 0 变 1 对不上。
        previous = {
            "status": row.status,
            "version": row.version,
            "reasonCodes": list(row.reason_codes or []),
            "comment": row.comment or "",
            "reviewedBy": row.reviewed_by,
            "reviewedAt": row.reviewed_at.isoformat() if row.reviewed_at else None,
        }
        history = [*(row.history or []), previous]
        # 评价可以反复改，不封顶会把这一行写成一本日志（旧实现同样截到 20 条）。
        row.status = payload.status
        row.version = event.version
        row.reason_codes = list(payload.reason_codes)
        row.comment = payload.comment
        row.reviewed_by = owner
        row.reviewed_at = event.reviewed_at
        row.history = history[-REVIEW_HISTORY_LIMIT:]
        await self.runs.upsert_review(row)
        await self.session.commit()
        return await self.detail(run_id, owner)

    # ------------------------------------------------------------------ #
    # 取件
    # ------------------------------------------------------------------ #
    async def input_file(self, run_id: str, asset_id: str, owner: str) -> Path:
        run = await self.runs.get_or_404(run_id, owner=owner)
        rows = await self.runs.inputs(run.id)
        match = next((row for row in rows if row.asset_id == asset_id), None)
        if match is None:
            raise NotFound("输入素材不存在")
        path = run_path(run.id) / "inputs"
        candidates = sorted(path.glob("reference-*")) + sorted(path.glob("reference-video.*"))
        # 按 position 定位：文件名是 `reference-<序号>`，序号来自 position+1。
        target = (
            next((p for p in candidates if p.name.startswith("reference-video")), None)
            if match.role == "source_video"
            else next(
                (p for p in candidates if p.name.startswith(f"reference-{match.position + 1}.")),
                None,
            )
        )
        if target is None or not target.is_file():
            raise NotFound("输入文件不存在")
        return target

    async def output(self, run_id: str, owner: str) -> Served:
        """成片取件：归档存储优先，工作区兜底。

        `output_key` 是「已经搬进归档存储」的凭证。没有它不代表没成片——重建前
        的老档案、以及上传失败的那些，文件仍在工作区里，本地兜底让它们照样能播，
        只是换台机器打不开。远端驱动必须走 307：几百 MB 的视频中转一次就是
        「内存 × 并发数」，而对象存储自己会处理 Range，能直接拖进度条。
        """
        run = await self.runs.get_or_404(run_id, owner=owner)
        if run.output_key:
            key = run.output_key
            url = await asyncio.to_thread(self.storage.download_url, key)
            if url is None:
                with self.storage.materialize(key) as path:
                    return Served.local(path, "video/mp4")
            if not await asyncio.to_thread(self.storage.exists, key):
                raise NotFound("成片字节不存在，请重新生成")
            return Served.remote(url, "video/mp4")
        result = run_path(run.id) / "outputs/generated.mp4"
        if not result.is_file():
            raise NotFound("原始结果尚未下载完成")
        return Served.local(result, "video/mp4")

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    async def _validate_inputs(
        self, payload: PrepareReplacement, owner: str, pack_owner: str,
        *, scope_required: bool = True,
    ) -> tuple[StoredMedia, list[StoredMedia]]:
        lookup = get_asset_lookup()
        media = await lookup.get_many(self.session, [payload.source_id, *payload.reference_ids])
        source = media.get(payload.source_id)
        if source is None or source.deleted_at:
            raise NotFound("原片不存在或已停止共享")
        # 公共原片池：别人的原片只有在 shared 时可用，这是模板库的核心设定
        # （选一段公共原片，把里面的人/产品/场景换成自己的）。
        if source.owner_id != owner and not source.shared:
            raise NotFound("原片不存在或已停止共享")
        if source.category != "source" or source.type != "video":
            raise BadRequest("请选择已归档的原片")
        if not 1 <= source.duration <= 15 or not source.has_audio:
            raise BadRequest("原片需为带音轨的完整 MP4，时长 1–15 秒；不会自动截断")
        if len(set(payload.reference_ids)) != len(payload.reference_ids):
            raise BadRequest("参考素材不能重复")
        refs: list[StoredMedia] = []
        for asset_id in payload.reference_ids:
            row = media.get(asset_id)
            if row is None or row.deleted_at:
                raise BadRequest("参考图不存在或已删除")
            if row.owner_id != pack_owner:
                raise BadRequest("参考图不属于该素材包")
            if row.category != payload.kind or row.type != "image":
                raise BadRequest("参考图分类需与人物/产品/场景替换类型一致")
            refs.append(row)
        texts = (payload.target, payload.notes, payload.replacement_scope)
        if any("{{" in text or "}}" in text for text in texts):
            raise BadRequest("请使用普通文字，不要手写素材引用标记")
        if not payload.target.strip() or (
            scope_required and payload.kind == "product" and not payload.replacement_scope.strip()
        ):
            raise BadRequest("请填写目标名称并选择产品替换范围")
        return source, refs

    def _output_available(self, run: ReplacementRun) -> bool:
        """成片还在不在：归档存储优先，工作区兜底（与 `output()` 同一套判断）。

        `output_key` 有值说明搬进归档存储了；没有则看工作区里那份——老档案、
        以及上传失败的那些，字节仍在本地。
        """
        if run.output_key:
            try:
                return bool(self.storage.exists(run.output_key))
            except (OSError, UpstreamError):
                return False
        # 归档根没挂上（`root()` 会抛 UpstreamError）、指针丢了（NotFound）、
        # 目录不可读（OSError）都按「没有成片」处理：列表里一条记录不该让整页 500。
        try:
            return (run_path(run.id) / "outputs/generated.mp4").is_file()
        except (NotFound, OSError, UpstreamError):
            return False

    async def _assert_still_authorized(self, run_id: str, owner: str) -> None:
        """提交前的最终授权：原片还可用、封装还已发布、档案还能提交。

        旧位置在 `libtv_process.run_node`（执行器的 submit 阶段），那里只有
        文件系统。细节与三处调用点见 `authorization.authorization_gap`——这里
        只负责把它变成用户看得懂的 400。
        """
        run = await self.runs.get_or_404(run_id, owner=owner)
        gap = await authorization_gap(self.session, run)
        if gap is not None:
            raise BadRequest(gap)

    async def _review_map(self, run_ids: list[str]) -> dict[str, ReviewRow]:
        """批量取评价：列表页逐条查会变成 N+1（旧实现逐条读 `review.json`）。

        返回的是**行**而不是出参：转换统一在 `_run_out` 里做一次。这里若先转成
        `schema.ReplacementReview`，`_run_out` 就得同时接受「行」和「出参」两种
        类型（详情接口走 `runs.review()` 拿的是行），类型上只能写成联合类型，
        调用方从此分不清手里是什么。
        """
        if not run_ids:
            return {}
        from sqlalchemy import select

        stmt = select(ReviewRow).where(ReviewRow.run_id.in_(run_ids))
        rows = (await self.session.scalars(stmt)).all()
        return {row.run_id: row for row in rows}

    def _run_out(self, run: ReplacementRun, review: ReviewRow | None) -> ReplacementRunOut:
        summary = (
            ReplacementInputSummary.model_validate(run.input_summary)
            if run.input_summary
            else None
        )
        prompt_mode, template_name = _prompt_origin(run.prompt_spec)
        return ReplacementRunOut(
            id=run.id,
            kind=run.kind,  # type: ignore[arg-type]
            target=run.target,
            status=run.status,
            archive_path=str(run_path(run.id)),
            created_at=run.created_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            elapsed_sec=elapsed_seconds(run),
            prompt=run.prompt,
            prompt_mode=prompt_mode,
            video_template_name=template_name,
            display_title=_display_title(
                run, _source_name(summary) if summary else None
            ),
            task_id=run.task_id,
            estimated_credits=run.estimated_credits,
            credits=run.credits,
            progress=run.progress_percent,
            review_status=(review.status if review else "pending"),
            review=_review_out(review) if review else None,
            input_summary=summary,
            last_error=run.last_error,
            output_url=(
                f"/api/v1/templates/replacement-runs/{run.id}/output"
                if run.output_key
                else None
            ),
            deleted_at=run.deleted_at,
            billing_mode="credits" if run.billing_mode == "credits" else "free",
        )


# --------------------------------------------------------------------------- #
# 纯函数
# --------------------------------------------------------------------------- #
def _fingerprint(owner: str, payload: PrepareReplacement) -> str:
    return hashlib.sha256((owner + payload.model_dump_json()).encode()).hexdigest()


def _role_labels(kind: str) -> dict[str, str]:
    """用途 key → 中文名，用来把「缺哪张图」说成人话。

    必须按素材包类型取：`front` 在产品包是「产品主图」、在人物包是「正面身份图」，
    合并所有词表会让错误提示指错东西（缺主图却说缺身份图）。
    """
    return dict(PACK_ROLES.get(kind) or {})


def _check_video_template(
    template: VideoTemplateOut, roles: dict[str, str], kind: str
) -> None:
    """模板按**原片**套用，图片按用途对应：换素材包时提示词与编号都不动。

    所以这里校验的是「当前素材包能不能凑出模板要的那几种用途」，而不是
    某几张固定 asset：素材包换图、升版都不再要求重新封装；凑不齐用途才拦下，
    并且要说清缺的是哪一张。
    """
    validate_options(template.prompt, template.rules)
    images = [item for item in template.media_order if item.type == "image"]
    provided = set(roles.values())
    needed = dict.fromkeys(item.role for item in images if item.role)
    missing = [role for role in needed if role not in provided]
    if missing:
        labels = _role_labels(kind)
        names = "、".join(labels.get(role, role) for role in missing)
        raise BadRequest(
            f"当前素材包缺少「{names}」，套用不了视频模板「{template.name}」；"
            "请换一个素材包，或取消模板改用通用提示词"
        )
    # 编号上界按**当前包**的图数算（`{{Mixed 1}}` 是原片，其后是 refs）。
    # 按封装时的图片数校验会放过「换了一个图更少的包」这种越界：mixedList 只有
    # N 项，提示词却写着 {{Mixed N+1}}，执行器拿不到那一项。
    upper = len(roles) + 1
    bad = [
        token
        for token in re.findall(r"\{\{([^{}]+)\}\}", template.prompt)
        if token != "ReplacementScope"
        and (not re.fullmatch(r"Mixed \d+", token) or not 1 <= int(token[6:]) <= upper)
    ]
    if bad:
        raise BadRequest(
            f"视频模板提示词含越界引用：{'、'.join(bad)}"
            f"（当前素材包共 {len(roles)} 张图，最大可用 {{{{Mixed {upper}}}}}）"
        )


def _media_in_mixed_order(
    template: VideoTemplateOut | None,
    source: StoredMedia,
    refs: list[StoredMedia],
    roles: dict[str, str] | None = None,
) -> list[StoredMedia]:
    """mixedList 的顺序：普通原片沿用「参考图 + 原片」；视频模板按**用途**排。

    `{{Mixed 1}}` 永远是原片；第 2 位起按模板声明的用途（主图 / 三视图 / 细节）
    从**当前素材包**里取图。于是同一份提示词配上不同的产品包，`{{Mixed 2}}` 永远
    是那个包的正面主图、`{{Mixed 3}}` 永远是三视图，封装一次就能到处用。
    模板没声明用途、或包里还有没用上的图，按原序补在后面，一张都不少。
    """
    if template is None:
        return [*refs, source]
    by_role = roles or {}
    remaining = list(refs)
    ordered: list[StoredMedia] = []
    for item in template.media_order:
        if item.type != "image":
            continue
        picked = None
        if item.role:
            picked = next((ref for ref in remaining if by_role.get(ref.id) == item.role), None)
        if picked is None and item.asset_id:
            # 没写用途的老模板 / 用途对不上时，退回按封装时那张图
            picked = next((ref for ref in remaining if ref.id == item.asset_id), None)
        if picked is not None:
            remaining.remove(picked)
            ordered.append(picked)
    return [source, *ordered, *remaining]


def _display_title(run: ReplacementRun, source_name: str | None) -> str:
    """统一展示名：规则在 `titles.py`，这里只负责把数据库行翻译成参数。"""
    return task_name(source_name, run.kind, run.target, run.id)


def _prompt_origin(spec: dict[str, object] | None) -> tuple[str, str | None]:
    """这条提示词是「封装时写死的」还是「按素材现编的」，以及出自哪条模板。

    **为什么要在档案里回答这个**：两种提示词长得一样，用户没法从文本判断
    自己写的那份有没有被改写。`prompt_mode=fixed` 明确告诉他「跑的就是你
    封装时那份原文」；`compiled` 则是按素材与替换范围现编的。

    `videoTemplateName` 是 prepare 时写进 spec 的；早期档案只有 id，
    这时才去模板库里反查一次（模板库是本机 JSON，查不到就只报 fixed）。
    """
    if not spec or spec.get("promptMode") != "fixed_video_template":
        return "compiled", None
    name = spec.get("videoTemplateName")
    if isinstance(name, str) and name.strip():
        return "fixed", name.strip()
    template_id = spec.get("videoTemplateId")
    if isinstance(template_id, str) and template_id:
        return "fixed", _template_name(template_id)
    return "fixed", None


def _template_name(template_id: str) -> str | None:
    """老档案只存了模板 id：反查名字，读不到就当没有（展示层自己降级）。

    缓存以目录文件的 mtime 为代次：模板重新封装、改名或置顶后目录被重写，
    mtime 一变缓存自然失效，不会让档案列表一直显示旧名字。
    """
    return _cached_template_name(template_id, video_template_store.catalog_stamp())


@lru_cache(maxsize=64)
def _cached_template_name(template_id: str, _stamp: float) -> str | None:
    try:
        return video_template_store.get_out(template_id).name
    except Exception:  # noqa: BLE001 - 模板被删/目录损坏都不该让档案列表 500
        logger.debug("视频模板反查失败 template_id=%s", template_id, exc_info=True)
        return None


def _source_name(summary: ReplacementInputSummary) -> str | None:
    return next(
        (item.name for item in summary.assets if item.role == "source_video"),
        None,
    )


def _check_scope(
    scopes: list[str], payload: PrepareReplacement, template: VideoTemplateOut | None = None
) -> None:
    """产品替换位置必须落在素材包已审核的范围内，运营不能临时扩大。"""
    if template is not None:
        allowed = scope_options(template.rules)
        chosen = [item.strip() for item in payload.replacement_scope.split("、") if item.strip()]
        if not allowed and not chosen:
            return
        if not allowed or not chosen or any(item not in allowed for item in chosen):
            raise BadRequest("请选择当前提示词封装的替换范围：" + "、".join(allowed))
        return
    if payload.kind != "product":
        return
    allowed = [PACK_SCOPES[scope] for scope in scopes if scope in PACK_SCOPES]
    if not allowed:
        raise BadRequest("素材包未声明可替换范围，请先在封装里补齐后重新发布")
    chosen = [item.strip() for item in payload.replacement_scope.split("、") if item.strip()]
    if not chosen:
        raise BadRequest("请选择产品替换位置（素材包已审核：" + "、".join(allowed) + "）")
    beyond = [item for item in chosen if item not in allowed]
    if beyond:
        raise BadRequest("替换范围超出素材包已审核的支持范围：" + "、".join(allowed))


def _materialize_inputs(
    storage: ArchiveStorage,
    path: Path,
    payload: PrepareReplacement,
    source: StoredMedia,
    refs: list[StoredMedia],
    frozen,
    video_template: VideoTemplateOut | None = None,
    roles: dict[str, str] | None = None,
) -> tuple[dict[str, object], Compilation, ReplacementInputSummary]:
    """把输入落到工作区并编译提示词。**同步函数**：调用方放进线程池。

    这一段刻意不碰数据库：它慢（物化 + Node 编译）、可能抛各种 IO 异常，
    混进事务会让「一次准备」持有数据库连接好几秒。

    `video_template` 非空时走固定提示词通道：媒体顺序按模板声明的**用途**从当前
    素材包里挑图，最终文案用模板原文；`bindings` / `mixedList` 仍由通用编译器按
    同一顺序产出，执行器那套「顺序、类型、摘要」校验一个都不绕过。
    """
    ensure_layout(path)
    media = _media_in_mixed_order(video_template, source, refs, roles)
    bindings: list[dict[str, str]] = []
    for index, item in enumerate(media):
        with storage.materialize(item.path) as original:
            filename = (
                "reference-video.mp4"
                if item is source
                else f"reference-{index + 1}{original.suffix}"
            )
            # 不重算 sha256：素材 key 就是摘要，物化出的字节与它同源。
            # 原先一次 prepare 会把同一个 300MB 原片读三遍（校验、copy、copy 后校验）。
            storage.link(original, path / "inputs" / filename, item.sha256)
        binding = {
            "id": item.id,
            "role": "source_video" if item is source else _REFERENCE_ROLE[payload.kind],
            "type": item.type,
            "file": f"inputs/{filename}",
        }
        # 同一用途下的多张图靠 usage 区分：主图定外观、多视图只补结构、细节图只补局部。
        usage_text = _usage_for_asset(item, refs, frozen, payload.kind)
        if usage_text:
            binding["usage"] = usage_text
        bindings.append(binding)

    # 执行器会给 inputs 下每个文件算一遍 sha256；同一份原片存两份等于白算。
    storage.link(
        path / "inputs/reference-video.mp4",
        path / "inputs/source-original.mp4",
        source.sha256,
    )

    variables = {"state_constraints": payload.notes, "replacement_scope": payload.replacement_scope}
    if frozen.features or frozen.instructions:
        variables["state_constraints"] = "\n".join(
            text for text in (frozen.features, frozen.instructions, payload.notes) if text
        )
    if payload.kind == "character":
        variables.update(
            source_subject="原片唯一前景主角，背景人物不替换",
            identity_description=f"目标人物为{payload.target}，多张参考属于同一人。",
        )
    elif payload.kind == "product":
        variables.update(
            source_product="原片中指定展示的产品",
            target_product=payload.target,
            replacement_scope=payload.replacement_scope or (
                "按封装提示词指定的范围" if video_template is not None else ""
            ),
        )
    else:
        variables.update(
            target_scene=payload.target,
            protected_foreground="原片人物、商品、手持物与所有交互前景对象",
        )

    duration = min(15, max(1, round(source.duration)))
    # 这里刻意从 `str` 起步：模板可以覆盖成任意画幅，不能把变量钉死成请求模型的 Literal。
    aspect_ratio = str(payload.aspect_ratio)
    if video_template is not None:
        # 封装时定下的档位优先：样片 9:16 / 5 秒，和模板里的 generation 对齐。
        generation = video_template.generation
        aspect_ratio = str(
            generation.get("ratio") or video_template.aspect_ratio or aspect_ratio
        )
        configured = generation.get("duration")
        if isinstance(configured, int):
            duration = min(15, max(2, configured))
    spec: dict[str, object] = {
        "task": payload.kind,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "wardrobe_mode": payload.wardrobe_mode,
        "references": bindings,
        "variables": variables,
    }
    if video_template is not None:
        spec["promptMode"] = "fixed_video_template"
        spec["videoTemplateId"] = video_template.id
        # 名字一起存进档案：列表和弹窗要回答「这条提示词是谁写的」，
        # 只给 id 的话得反查模板库，而模板库是本地 JSON，换台机器就没有了。
        spec["videoTemplateName"] = video_template.name
        spec["fixedPromptSha256"] = video_template.prompt_sha256
        spec["fixed_prompt"] = video_template.prompt
    compiled = compile_prompt(spec, node=get_libtv_settings().node)
    if payload.kind == "character" and payload.wardrobe_mode == "reference":
        compiled.prompt = compiled.prompt.replace("；提供同片原音轨", "；原片内嵌音轨提供声音")
        compiled.prompt = compiled.prompt.replace(
            "声音：为完整原片音轨", "声音：原片内嵌音轨为完整原片音轨"
        )
        compiled.compiler_version += "+embedded-audio-v1"

    (path / "inputs/prompt.txt").write_text(compiled.prompt, encoding="utf-8", newline="\n")
    storage.write(path / "inputs/prompt-spec.json", spec)
    storage.write(path / "inputs/configuration.json", payload.model_dump(mode="json"))
    # 这两份是**执行器**的输入，不是给人看的：`bridge.mjs` 读 input-map 逐条上传、
    # 再从 media-inputs 取宽高时长拼请求体，最后拿 run.json 的 `file_checksums`
    # 与实算摘要比对。少一份它就在 upload 阶段直接抛错，而那时钱还没花、
    # 用户只看到「上传准备未完成」，无从判断是缺文件。
    storage.write(
        path / "inputs/input-map.json",
        [{**binding, "sha256": item.sha256, "name": item.name} for binding, item in
         zip(bindings, media, strict=True)],
    )
    storage.write(
        path / "records/media-inputs.json",
        {
            binding["file"]: {
                "width": item.width,
                "height": item.height,
                "duration": item.duration,
            }
            for binding, item in zip(bindings, media, strict=True)
        },
    )
    storage.write(
        path / "records/prompt-compilation.json",
        compiled.model_dump(),
    )
    # 编译器快照：代码改了行为却没变时，靠它证明「当时跑的是哪一版」
    shutil.copytree(RUNTIME, path / "code/runtime", dirs_exist_ok=True)
    shutil.copyfile(BRIDGE, path / "code/runtime/bridge.mjs")

    summary = ReplacementInputSummary(
        assets=[
            ReplacementInputAsset(
                id=item.id,
                name=item.name,
                role=binding["role"],
                type=item.type,
                # 指向**归档快照**而不是素材本身：素材后来被删或换了内容，评价页
                # 仍要能打开「当初喂给模型的那张图」，否则人工验收失去依据。
                url=(
                    f"/api/v1/templates/replacement-runs/{payload.request_id.hex}"
                    f"/inputs/{item.id}"
                ),
            )
            for item, binding in zip(media, bindings, strict=True)
        ],
        cover_reference_id=payload.reference_ids[0] if payload.reference_ids else None,
        wardrobe_mode=payload.wardrobe_mode,
        replacement_scope=payload.replacement_scope,
        notes=payload.notes,
    )
    (path / "00_本次调用说明.md").write_text(
        f"# {payload.target} · {payload.kind}\n\n输入已归档，尚未提交。\n"
        "inputs 保存实际文件副本；outputs 保存原始成片；records 保存状态和错误分类。\n"
        "提交前须取得估价回执（0 积分畅用队列须报 0，积分队列须报正数）；\n"
        "人工验收与生成完成分别记录。\n",
        encoding="utf-8",
    )
    return spec, compiled, summary


def _usage_for_asset(
    item: StoredMedia, refs: list[StoredMedia], frozen, kind: str
) -> str:
    """这张图在提示词里的说明：用途文字 + 封装里为它写的补充说明。"""
    try:
        index = refs.index(item)
    except ValueError:
        return ""
    if index >= len(frozen.bindings):
        return ""
    binding = frozen.bindings[index]
    if not isinstance(binding, dict):
        binding = PackBinding.model_validate(binding).model_dump()
    text = usage_for(kind, str(binding.get("role") or ""))
    detail = str(binding.get("description") or "").strip().rstrip("。.；;")
    if detail:
        text = f"{text}；{detail}" if text else detail
    return text


def _review_out(row: ReviewRow) -> ReplacementReview:
    return ReplacementReview(
        status=row.status,
        version=row.version,
        reason_codes=list(row.reason_codes or []),  # type: ignore[arg-type]
        comment=row.comment,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        history=[
            ReplacementReviewEvent.model_validate(item) for item in (row.history or [])
        ],
    )
