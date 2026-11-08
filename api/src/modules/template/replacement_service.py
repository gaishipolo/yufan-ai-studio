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
import shutil
from datetime import UTC, datetime
from pathlib import Path

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
from modules.template.authorization import authorization_gap
from modules.template.models import ReplacementReview as ReviewRow
from modules.template.models import ReplacementRun
from modules.template.pack_roles import PACK_SCOPES, usage_for
from modules.template.repository import PackRepository, RunRepository
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
from modules.template.workspace import (
    BRIDGE,
    RUNTIME,
    Compilation,
    compile_prompt,
    create_workspace,
    ensure_layout,
    run_path,
)

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
        _check_scope(frozen.supported_scopes, payload)

        source, refs = await self._validate_inputs(payload, owner, pack.owner_id)
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
                for position, item in enumerate([*refs, source])
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
        self, payload: PrepareReplacement, owner: str, pack_owner: str
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
            payload.kind == "product" and not payload.replacement_scope.strip()
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
        return ReplacementRunOut(
            id=run.id,
            kind=run.kind,  # type: ignore[arg-type]
            target=run.target,
            status=run.status,
            archive_path=str(run_path(run.id)),
            created_at=run.created_at,
            prompt=run.prompt,
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


def _display_title(run: ReplacementRun, source_name: str | None) -> str:
    """统一展示名：规则在 `titles.py`，这里只负责把数据库行翻译成参数。"""
    return task_name(source_name, run.kind, run.target, run.id)


def _source_name(summary: ReplacementInputSummary) -> str | None:
    return next(
        (item.name for item in summary.assets if item.role == "source_video"),
        None,
    )


def _check_scope(scopes: list[str], payload: PrepareReplacement) -> None:
    """产品替换位置必须落在素材包已审核的范围内，运营不能临时扩大。"""
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
) -> tuple[dict[str, object], Compilation, ReplacementInputSummary]:
    """把输入落到工作区并编译提示词。**同步函数**：调用方放进线程池。

    这一段刻意不碰数据库：它慢（物化 + Node 编译）、可能抛各种 IO 异常，
    混进事务会让「一次准备」持有数据库连接好几秒。
    """
    ensure_layout(path)
    media = [*refs, source]
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

    variables = {"state_constraints": payload.notes}
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
        "仅 0 积分提交；人工验收与生成完成分别记录。\n",
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
