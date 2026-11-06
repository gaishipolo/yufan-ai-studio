"""生图服务。

生成本身交给 `generation` 框架：这里只做「建记录 → 投递」，
提交/重试/超时/落结果全在 `generation.runner` 里，与其他生成能力共用一套。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import audit
from core.context import CurrentUser
from core.exceptions import BadRequest, Forbidden, InvalidState, UpstreamError
from core.logging import get_logger
from core.response import Page, PageParams
from db.repository import BaseRepository
from generation.runner import get_runner
from generation.spec import STATUS_FAILED, STATUS_QUEUED
from infra import get_model_router
from infra.ai.base import Capability, ModelSpec
from modules.image.models import ImageJob, remember_previous_external_id
from modules.image.permissions import PERM_READ_ALL, PERM_RETRY_ANY
from modules.image.pipeline import KIND_IMAGE
from modules.image.schema import GenerateImageIn, ImageJobOut

logger = get_logger(__name__)

_MAX_JOB_NAME = 120


def _validate_params(spec: ModelSpec, payload: GenerateImageIn) -> None:
    """按模型自己声明的 `param_schema` 校验入参，在**受理时**就拒绝。

    为什么不能留给供应商兜底：Agnes 对尺寸是敏感的——`1024×1536`（全角乘号）
    会被 400 拒掉，但那是**异步**才发生的，用户看到的是「生成中」，
    等几秒后只剩一个失败状态，而失败原因藏在 `errorMessage` 里没人看。
    受理时报错不但更快，还能把可选值一并告诉他。

    同理「张数」：供应商只支持 n=1，声明里写 4 就等于承诺了一个做不到的能力。
    """
    schema = spec.param_schema

    size_schema = schema.get("size") or {}
    options = size_schema.get("options") or ()
    if options and payload.size not in options:
        raise BadRequest(
            f"尺寸 {payload.size!r} 不可用于模型 {spec.id!r}，可选: {list(options)}"
            "（注意是英文字母 x，不是乘号 ×）"
        )

    count_schema = schema.get("count") or {}
    low = int(count_schema.get("min", 1))
    high = int(count_schema.get("max", payload.count))
    if not low <= payload.count <= high:
        limit = f"{low}-{high}" if low != high else str(high)
        raise BadRequest(f"张数 {payload.count} 超出模型 {spec.id!r} 的范围（{limit}）")


class ImageService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = BaseRepository(session, ImageJob)

    async def list_jobs(self, actor: CurrentUser, page: PageParams) -> Page[ImageJobOut]:
        """历史列表：数据范围只看 `image:read:all`，与 task 模块同一套规则。

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
        return Page.create(
            [ImageJobOut.model_validate(row) for row in rows], total, page
        )

    async def generate(self, actor: CurrentUser, payload: GenerateImageIn) -> ImageJob:
        """建记录并投递到生成队列，接口立即返回 `queued`。

        模型**在受理时就校验**，而不是等异步任务跑起来才发现写错：
        供应商没配置、模型名写错这类问题应当是 400，不是「提交成功但任务失败」。
        """
        router = get_model_router()
        model_id = payload.model or router.default_model(Capability.IMAGE)
        spec = router.resolve(model_id)
        if spec is None:
            raise BadRequest(
                f"未知模型 {model_id!r}，可用模型: "
                f"{[m.id for m in router.models(Capability.IMAGE)]}"
            )
        if spec.capability is not Capability.IMAGE:
            raise BadRequest(f"模型 {model_id!r} 不是生图模型")
        # 供应商未接入时在这里就暴露（provider_for 会抛 BadRequest）
        router.provider_for(spec.id)
        _validate_params(spec, payload)

        job = ImageJob(
            name=payload.prompt.strip()[:_MAX_JOB_NAME] or "未命名",
            prompt=payload.prompt,
            size=payload.size,
            count=payload.count,
            status=STATUS_QUEUED,
            model=spec.label,
            model_id=spec.id,
            owner_id=actor.id,
        )
        self.session.add(job)
        await self.session.commit()

        # 操作留痕：日志是给排障的，这条是给对账与追责的（谁、什么参数、哪张模型）
        await audit.record(
            "image.generate",
            actor_id=actor.id,
            target_type="image_job",
            target_id=job.id,
            detail={"model": spec.id, "size": payload.size, "count": payload.count},
        )

        await get_runner().dispatch(KIND_IMAGE, job.id)
        return job

    async def retry(
        self, actor: CurrentUser, job_id: str, *, confirm_charged: bool = False
    ) -> ImageJob:
        """人工重试：把 `failed` 复位成 `queued` 并重新投递。

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

        previous_external_id = job.external_id
        if previous_external_id and not confirm_charged:
            raise InvalidState(
                f"该次生成已提交到供应商（任务号 {previous_external_id}），可能已产生费用："
                "请先用任务号核对或找回结果；确认要重新生成时带 "
                "confirmCharged=true 重试"
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
        job.started_at = None
        job.finished_at = None
        await self.session.commit()

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
