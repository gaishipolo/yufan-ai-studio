"""生图服务。

生成本身交给 `generation` 框架：这里只做「建记录 → 投递」，
提交/重试/超时/落结果全在 `generation.runner` 里，与其他生成能力共用一套。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core import audit
from core.config import get_settings
from core.context import CurrentUser
from core.exceptions import (
    BadRequest,
    Conflict,
    Forbidden,
    InvalidState,
    RateLimited,
    UpstreamError,
)
from core.idempotency import fingerprint_matches, request_fingerprint
from core.library_refs import resolve_reference_images
from core.library_text import dumps_text_refs, freeze_text_refs, inject_text_refs
from core.logging import get_logger
from core.param_schema import resolve_params
from core.ports.submission_ledger import abandon_submission
from core.response import Page, PageParams
from db.repository import BaseRepository
from generation.runner import get_runner
from generation.spec import STATUS_FAILED, STATUS_QUEUED, STATUS_RUNNING
from infra import get_model_router
from infra.ai.base import Capability, ModelSpec
from infra.artifacts import artifact_urls
from modules.image.models import (
    ImageJob,
    dumps_params,
    dumps_references,
    remember_previous_external_id,
)
from modules.image.permissions import PERM_READ_ALL, PERM_RETRY_ANY
from modules.image.pipeline import (
    KIND_IMAGE,
    MAX_REFERENCE_BYTES,
    REFERENCE_MIME_BY_SUFFIX,
)
from modules.image.schema import GenerateImageIn, ImageJobOut

logger = get_logger(__name__)

#: 作业名上限。跟 `image_jobs.name` 的列宽（200）与视频任务标题
#: （`CreateTaskIn.title` 上限 200）对齐：模板名允许 160 字，模板库把它原样
#: 当任务名传，截在 120 会让同一模板的生图任务与视频任务在卡片上显示成两个名字。
_MAX_JOB_NAME = 200


def _reference_mime(storage_key: str, declared: str) -> str:
    """定参考图的 MIME：资产上登记了就用它，缺了按存储键后缀推。

    两条来源都要，因为资产表的 `mime_type` 允许为空（历史导入、探测失败）。
    推不出来就返回空串，由调用方拒绝——拿 `application/octet-stream` 当
    Data URI 的类型，供应商会当成非图片处理，报错信息离原因很远。
    """
    if declared.strip().lower() in REFERENCE_MIME_BY_SUFFIX.values():
        return declared.strip().lower()
    return REFERENCE_MIME_BY_SUFFIX.get(PurePosixPath(storage_key).suffix.lower(), "")


async def _resolve_references(
    session: AsyncSession,
    actor: CurrentUser,
    asset_ids: Sequence[str],
    spec: ModelSpec,
) -> list[dict[str, str]]:
    """把参考图的资产 id 解析成「提交时要用的快照」。

    准入判据（存在 / 可读 / 在库 / 是图片）在 `core.library_refs` 一处，
    视频任务那条链路也走它。这里只补生图特有的两件事：

    1. **张数上限**按模型声明的 `max_references`（`0` = 纯文生图）；
    2. **能不能交给供应商**：MIME 在白名单里、体积别太大（要转 base64 发出去）。

    返回顺序**与入参一致**：多图合成时提示词里的「第一张」指的是这个顺序。
    """
    limit = max(0, spec.max_references)
    if limit == 0:
        # 只有「真的带了参考图」才是错。纯文生图模型（`max_references == 0`，
        # 比如 echo 的示例模型）不带参考图时是正常请求，早先这里无条件 raise，
        # 结果是这类模型的**每一次生图都 400**。
        if asset_ids:
            raise BadRequest(
                f"模型 {spec.label} 不支持参考图（纯文生图），"
                "请换一个支持图生图 / 多图合成的模型"
            )
        return []
    refs = await resolve_reference_images(session, actor, asset_ids, limit=limit)

    references: list[dict[str, str]] = []
    for ref in refs:
        mime = _reference_mime(ref.storage_key, ref.mime_type)
        if not mime:
            raise BadRequest(
                f"参考图 {ref.name or ref.id} 的格式不受支持，请换 png / jpeg / webp 的图"
            )
        if ref.size_bytes > MAX_REFERENCE_BYTES:
            raise BadRequest(
                f"参考图 {ref.name or ref.id} 超过 "
                f"{MAX_REFERENCE_BYTES // (1024 * 1024)}MB，请先压缩后再用"
            )
        references.append({"assetId": ref.id, "storageKey": ref.storage_key, "mimeType": mime})
    return references


def _validate_params(spec: ModelSpec, payload: GenerateImageIn) -> dict[str, object]:
    """按模型声明的 `param_schema` 校验入参。

    规则本身在 `core.param_schema`：统一生成门面也要用同一份，否则「同一个
    模型在两条路径上接受不同的值」，用户照着生图页的提示填参数换条路径就 400。

    `size` / `count` 是接口上的历史顶层字段，非空时优先于 `params` 里的同名键
    ——同一份请求里两处都写时，语义得有个确定的答案，否则重试与首提可能用不同值。
    """
    return resolve_params(
        spec.id,
        spec.param_schema or {},
        payload.params,
        top_level={"size": payload.size, "count": payload.count},
    )


class ImageService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = BaseRepository(session, ImageJob)

    async def list_jobs(self, actor: CurrentUser, page: PageParams) -> Page[ImageJobOut]:
        """历史列表：数据范围只看 `image:read:all`，与 task 模块同一套规则（手册 §1.1）。

        只按 `owner_id` 过滤时，管理员在「全部」视图里也只看得到自己的历史，
        而任务列表里能看到全站——同一份工作台两套答案，用户只会以为是 bug
        （R4-07 / R6-09）。

        分页不是可选项：范围打开之后，这个接口对 `image:*` 的账号就是全表返回，
        而前端一进生图历史页就会调它。`total` 走 `count(*)`，不是 `len(rows)`——
        后者在「有 200 条、这页 24 条」时会把总数报成 24（R7-02）。
        """
        # 排序键必须以主键收尾，否则同一时间戳的行在翻页边界上顺序不定，
        # 会重复或漏掉（sqlite 按 rowid 兜底，换 PG 就现形）——R8-06
        stmt = select(ImageJob).order_by(ImageJob.created_at.desc(), ImageJob.id.desc())
        if not actor.has(PERM_READ_ALL):
            stmt = stmt.where(ImageJob.owner_id == actor.id)
        rows, total = await self.repo.paginate(stmt, page)
        return Page.create(await self.to_out_many(rows), total, page)

    async def out(self, job: ImageJob) -> ImageJobOut:
        """单条出参（提交、重试后用）。"""
        return (await self.to_out_many([job]))[0]

    async def to_out_many(self, rows: Sequence[ImageJob]) -> list[ImageJobOut]:
        """出参统一在这里把落库的**对象 key** 换成可访问地址。

        库里的值可能是三种：对象 key（新）、老 `/static/<key>`（迁移前）、
        供应商直链（没有落盘的能力）。`artifact_urls` 只对第一种动手，
        所以这里不需要版本判断。签名只为「本页要显示的图」做一次批量，
        不要逐条 `get_storage()`——那会给每张图重建一次驱动实例。
        """
        flat = [value for row in rows for value in (row.output_url, row.cover_url)]
        urls = await artifact_urls(flat)
        items: list[ImageJobOut] = []
        for index, row in enumerate(rows):
            item = ImageJobOut.model_validate(row)
            item.output_url = urls[index * 2] or None
            item.cover_url = urls[index * 2 + 1]
            items.append(item)
        return items

    async def _assert_in_flight_room(self, actor: CurrentUser) -> None:
        """一个人攒几十条生图，占的是**共享上游**的位次，而他并没有因此更快。

        与视频任务同一口径（`modules/task/service.py::_assert_in_flight_room`）：
        判据是 queued + running 的条数，上限来自 `IMAGE_MAX_IN_FLIGHT_PER_USER`，
        `0` = 不限。

        为什么频率限流不够：`image.generate` 每分钟 60 次，拦得住「手抖连点」，
        拦不住「一分钟点 60 次」。而每条生图都要占供应商账号的上游排队位次
        （账号是全团队共享的）——挂 60 条的人把别人的生图排到自己后面，
        自己也一样在队尾。

        注意这里压的**不是** worker 槽位：等出图不占槽位，槽位只在提交 /
        下载那几秒被占。所以「调大并发」解决不了这件事。
        """
        limit = get_settings().image_max_in_flight_per_user
        if limit <= 0:
            return
        stmt = select(ImageJob).where(
            ImageJob.owner_id == actor.id,
            ImageJob.status.in_((STATUS_QUEUED, STATUS_RUNNING)),
        )
        in_flight = await self.repo.count(stmt)
        if in_flight >= limit:
            raise RateLimited(
                f"你已有 {in_flight} 条生图在排队或生成中（上限 {limit} 条）："
                "等其中几条跑完再提交，否则会挤占别人的排队时间"
            )

    async def _idempotent_job(
        self, actor_id: str, key: str, fingerprint: str
    ) -> ImageJob | None:
        """同键重放：同内容返回原记录，不同内容 409。"""
        row = await self.session.scalar(
            select(ImageJob).where(
                ImageJob.owner_id == actor_id,
                ImageJob.idempotency_key == key,
            )
        )
        if row is None:
            return None
        if not fingerprint_matches(row.idempotency_fingerprint, fingerprint):
            raise Conflict(
                "该幂等键已经用于另一份生图请求：同一次提交请复用原键，"
                "新的提交请生成新的幂等键"
            )
        return row

    async def generate(self, actor: CurrentUser, payload: GenerateImageIn) -> ImageJob:
        """建记录并投递到生成队列，接口立即返回 `queued`。

        模型**在受理时就校验**，而不是等异步任务跑起来才发现写错：
        供应商没配置、模型名写错这类问题应当是 400，不是「提交成功但任务失败」。

        带 `idempotencyKey` 时，同键同内容直接返回原记录（不重复提交供应商）；
        不同键仍是两次独立提交，可以并发。
        """
        key = payload.idempotency_key
        fingerprint = request_fingerprint(payload) if key else ""
        if key:
            # 放在容量检查之前：已经受理过的重放不该被“在途已满”挡住。
            existing = await self._idempotent_job(actor.id, key, fingerprint)
            if existing is not None:
                return existing
        # 在途上限放在最前面：这一步不依赖 payload，被挡住时不该先花时间做
        # 模型解析与参考图解析（那是「提交被拒」时最不该付的代价）。
        await self._assert_in_flight_room(actor)
        router = get_model_router()
        model_id = payload.model or router.default_model(Capability.IMAGE)
        spec = router.resolve(model_id)
        if spec is None:
            # 提示里只用公开模型：把 `internal` / `placeholder` 模型列在这里，
            # 等于引导用户再错一次（选中必然 400「调用档案标识不匹配」）
            raise BadRequest(
                f"未知模型 {model_id!r}，可用模型: "
                f"{[m.id for m in router.public_models(Capability.IMAGE)]}"
            )
        if spec.capability is not Capability.IMAGE:
            raise BadRequest(f"模型 {model_id!r} 不是生图模型")
        # 供应商未接入时在这里就暴露（provider_for 会抛 BadRequest）
        router.provider_for(spec.id)
        params = _validate_params(spec, payload)
        references = await _resolve_references(
            self.session, actor, payload.reference_library_ids, spec
        )
        # 文本在**受理期**冻结：提交 / 重试只读这份快照，资产之后被编辑也不会
        # 让同一条任务的两跳用上不同的提示词。校验 `{{Text N}}` 编号也在这里，
        # 写错了当场 400，而不是等异步任务失败。
        text_refs = await freeze_text_refs(
            self.session,
            actor,
            payload.text_library_ids,
            prompt=payload.prompt,
        )

        # 尺寸 / 张数在表里是独立列（历史列表、卡片角标都按列读），其余参数
        # 按模型声明自由扩展，统一进 params_json。别把它们并进同一条 JSON：
        # 那样 `size` 的默认值就得靠解析失败兜底，排序与统计也会失真。
        size = str(params.get("size") or payload.size or "1024x1024")
        count = int(params.get("count") or payload.count or 1)  # type: ignore[call-overload]
        extra = {key: value for key, value in params.items() if key not in ("size", "count")}

        # 作业名给人看（生图历史 / 任务中心卡片）。
        # 优先用提交方给的名字（模板库传模板名）：一长段提示词会把卡片挤满，
        # 也与同名模板的视频任务对不上。留空时才退回从提示词派生 ——
        # 直接提交（不经过模板库）的生图没有模板名，仍按老行为取名。
        # 派生时把 `{{Text N}}` 换成正文；真正的 prompt 列保持原样（重试读它）。
        given = payload.name.strip()
        display = inject_text_refs(payload.prompt, text_refs).strip()
        job = ImageJob(
            name=(given or display)[:_MAX_JOB_NAME] or "未命名",
            prompt=payload.prompt,
            size=size,
            count=count,
            params_json=dumps_params(extra),
            reference_json=dumps_references(references),
            text_json=dumps_text_refs(text_refs),
            status=STATUS_QUEUED,
            model=spec.label,
            model_id=spec.id,
            owner_id=actor.id,
            idempotency_key=key or None,
            idempotency_fingerprint=fingerprint or None,
        )
        self.session.add(job)
        try:
            await self.session.commit()
        except IntegrityError:
            # 并发同键：唯一索引兜底；回读那一条并按指纹决定重放还是 409。
            await self.session.rollback()
            if key:
                raced = await self._idempotent_job(actor.id, key, fingerprint)
                if raced is not None:
                    return raced
            raise

        # 操作留痕：日志是给排障的，这条是给对账与追责的（谁、什么参数、哪张模型）
        await audit.record(
            "image.generate",
            actor_id=actor.id,
            target_type="image_job",
            target_id=job.id,
            detail={
                "model": spec.id,
                "size": size,
                "count": count,
                **extra,
                # 参考图留痕只记 id：字节内容没必要进审计日志，但「用了别人的哪张图」
                # 要能查出来（尤其是在排查「生成结果像某个素材」的时候）
                "referenceLibraryIds": [item["assetId"] for item in references],
                # 文本留痕只记 id + 版本：正文进审计日志既浪费也含用户内容，
                # 但「这一版提示词用的是哪一稿」必须能查。
                "textLibraryIds": [item["assetId"] for item in text_refs],
                "textVersions": [item["version"] for item in text_refs],
            },
        )

        await get_runner().dispatch(KIND_IMAGE, job.id)
        return job

    async def delete_jobs(
        self, ids: list[str], actor: CurrentUser
    ) -> tuple[int, list[str]]:
        """批量删除生成历史，返回（删除条数, 被跳过的 id）。

        与 `DELETE /tasks` 同一口径：

        - 可见性只看「本人」或 `image:read:all`（数据范围开关）；
        - 「不存在」与「没权限」都记为 skipped，对外不区分（不向无权限者泄漏
          「这个 id 存在」），细节进服务端日志；
        - 一条越权不该让整批 403，但调用方必须能看见哪些没删成。

        **删除即放弃重试**（用户 2026-11-15 拍板）：失败记录上的 `external_id`
        原本是核对"是否已扣费"的凭证，删了就没有可重试、可核对的入口了。
        这是明确选择，不是遗漏。产物字节不在这里删：图片已进资产库，
        对象存储的生命周期归库自己的回收，删一条历史不该顺带毁掉一份素材。
        """
        if not ids:
            return 0, []
        can_read_all = actor.has(PERM_READ_ALL)
        rows = await self.session.scalars(select(ImageJob).where(ImageJob.id.in_(ids)))
        owned = [row for row in rows if can_read_all or row.owner_id == actor.id]
        owned_ids = {row.id for row in owned}
        skipped = [job_id for job_id in ids if job_id not in owned_ids]
        for row in owned:
            await self.session.delete(row)
        await self.session.commit()
        if skipped:
            logger.warning(
                "生图记录批量删除跳过 %d 条（不存在或无权限）: %s",
                len(skipped),
                skipped[:20],
            )
        return len(owned), skipped

    async def retry(
        self, actor: CurrentUser, job_id: str, *, confirm_charged: bool = False
    ) -> ImageJob:
        """人工重试：把 `failed` 复位成 `queued` 并重新投递（手册 §5.6②）。

        这里必须**显式清掉提交意图标记**（`submit_attempts`）。否则记录带着
        `attempts>0 且 external_id 为空` 的形状回到队列，`handle_submit` 会把它
        判成「提交结果未知」并再次置死——用户点多少次重试都是同一个结果，
        而 `SUBMIT_UNKNOWN_MESSAGE` 偏偏写着「请核对后手动重试」（R11-02）。

        与 `task/service.py::retry_task` 同一口径：可见性与「替别人花钱」是两个
        独立开关，先过 `list_jobs` 的可见性会让只有 retry:any 的客服永远 403。

        **两种 failed 不能共用一个出口（R12-01）**：
        - `attempts>0` 且无 `external_id` = 没确认受理，重试安全；
        - 有 `external_id` = 供应商已经受理（同步供应商甚至已经出图扣费），
          恢复器把它置成 failed 只是因为「续查不了」。此时重投就是第二次
          `provider.submit()`，而清掉 `external_id` 连对账凭证都没了。
          所以默认拒绝，必须带 `confirm_charged=True` 显式确认；确认前把旧
          任务号搬进 `previous_external_id` 并写进审计。
        """
        job = await self.repo.get_or_404(job_id, "生图记录不存在")
        if job.owner_id != actor.id and not actor.has(PERM_RETRY_ANY):
            raise Forbidden("无权重试他人生图任务")
        if job.status != STATUS_FAILED:
            raise InvalidState("仅失败的生图任务可以重试")

        # 重试也是「往队列里加一条」：与 generate 同一道在途上限。
        # 放在状态/权限之后、改现场之前：被拒时不该先清掉 external_id，
        # 让明确的错误（无权 / 不是失败任务）先赢。
        await self._assert_in_flight_room(actor)

        previous_external_id = job.external_id
        if previous_external_id and not confirm_charged:
            raise InvalidState(
                f"该次生成已提交到供应商（任务号 {previous_external_id}），可能已产生费用："
                "请先用任务号核对或找回结果；确认要重新生成时带 "
                "confirmCharged=true 重试",
                details={
                    "reason": "confirm_charged_required",
                    "confirmChargedRequired": True,
                    "externalId": previous_external_id,
                },
            )

        # 清 external_id 之前先留凭证：记录上留一份（页面/DB 可查），
        # 审计里留一份（谁确认的、旧任务号是什么）。审计写入按设计会吞异常，
        # 所以不能只靠它；而记录只有一个槽位，第二次确认会覆盖第一次，
        # 因此同时追加进 external_id_history（R13-02）。
        history: list[str] = []
        if previous_external_id:
            history = remember_previous_external_id(job, previous_external_id)
        job.status = STATUS_QUEUED
        job.progress = 0
        job.error_message = None
        job.external_id = None
        job.submit_attempts = 0
        job.output_url = None
        job.cover_url = ""
        job.submit_mode = None  # 重新提交时由 mark_submitted 重写快照
        # 新一轮从现在起算：清成 None 会让框架回落到 `created_at`，
        # 重试一条刚超时的任务就会立刻再超时。
        job.started_at = datetime.now(tz=UTC)
        job.finished_at = None
        await self.session.commit()

        # 与视频链同一口径：业务表清号后必须让台账也忘掉上一轮，
        # 否则按记录 id 取凭证的驱动会把旧任务号当成本轮提交结果返回。
        await abandon_submission(job_id)

        await audit.record(
            "image.retry",
            actor_id=actor.id,
            target_type="image_job",
            target_id=job.id,
            detail={
                "previousExternalId": previous_external_id,
                # 全量历史一并入审计：单槽被覆盖时这里仍有完整链路
                "previousExternalIds": history,
                "confirmedCharged": confirm_charged,
            },
        )
        await self._dispatch_after_reset(job)
        return job

    async def _dispatch_after_reset(self, job: ImageJob) -> None:
        """复位后投递；投递失败不能只抛 500 把现场留在「已复位但没入队」。

        记录此刻是 `queued` + `attempts=0` 的形状，正是巡检
        （`maintenance.reconcile_stale_jobs`）负责接手的那一类。所以这里
        只需要把「已受理、会自动重投」说清楚，不要让用户以为要再点一次——
        重试按钮此时已经不生效了（记录不是 `failed`），这才是真正的坑（R13-07）。
        """
        try:
            await get_runner().dispatch(KIND_IMAGE, job.id)
        except Exception as exc:  # noqa: BLE001 - 队列不可用不该吞掉，但要给出可操作的说明
            logger.exception("生图重试已复位但投递失败 id=%s，等待巡检重投", job.id)
            raise UpstreamError(
                "重试已受理（记录已复位为排队），但投递队列暂时不可用；"
                "系统会在几分钟内自动重投，无需重复点击"
            ) from exc
