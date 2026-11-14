"""任务领域服务：业务规则都在这一层，路由只做协议转换。

事务边界明确：一次 service 方法 = 一次提交。

注意这里的 service **不认识供应商、不认识队列协议细节**：
创建任务只调用 `runner.dispatch()`，提交/轮询/重试/事件都由生成框架负责。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import BadRequest, Forbidden, InvalidState, UpstreamError
from core.library_refs import resolve_reference_images
from core.library_text import dumps_text_refs, freeze_text_refs
from core.logging import get_logger
from core.ports.library_lookup import LibraryAssetKind
from core.response import PageParams
from generation.runner import get_runner
from infra.ai.base import Capability
from infra.ai.router import ModelRouter
from infra.artifacts import artifact_urls
from infra.queue.base import JobQueue
from modules.task.enums import AspectRatio, TaskScope, TaskStatus
from modules.task.models import VideoTask
from modules.task.params import resolve_aspect_ratio, resolve_duration, resolve_model
from modules.task.permissions import PERM_READ_ALL, PERM_RETRY_ANY
from modules.task.pipeline import KIND_VIDEO
from modules.task.repository import TaskRepository
from modules.task.schema import (
    CreateTaskIn,
    TaskListOut,
    TaskListQuery,
    TaskOut,
    TaskRetryOut,
    TaskStats,
)

logger = get_logger(__name__)

DEFAULT_TITLE = "未命名任务"


class TaskService:
    def __init__(
        self,
        session: AsyncSession,
        repo: TaskRepository,
        queue: JobQueue,
        router: ModelRouter,
    ) -> None:
        self.session = session
        self.repo = repo
        self.queue = queue
        self.router = router

    # ------------------------------------------------------------------ 查询
    async def list_tasks(
        self, actor: CurrentUser, query: TaskListQuery, page: PageParams
    ) -> TaskListOut:
        base = self.repo.base_query(query, actor)
        stats = await self.repo.count_by_status(base)

        stmt = self.repo.apply_sort(self.repo.apply_status(base, query.status), query.sort)
        rows, total = await self.repo.paginate(stmt, page)

        items = await self.present_many(rows)
        return TaskListOut(
            items=items,
            total=total,
            page=page.page,
            page_size=page.page_size,
            stats=stats,
            can_read_all=actor.has(PERM_READ_ALL),
        )

    async def stats(self, actor: CurrentUser, scope: TaskScope = TaskScope.MINE) -> TaskStats:
        """按 scope 统计（与列表同一套降级规则），保证角标与列表数字同口径。

        默认仍按 MINE——侧边栏角标是「我的待办」，不该因为账号有 read:all
        就把别人的任务数算进来；管理员在看「全部」时前端应传 scope=all。
        """
        stmt = self.repo.base_query(TaskListQuery(scope=scope), actor)
        return await self.repo.count_by_status(stmt)

    async def get_task(self, task_id: str, actor: CurrentUser | None = None) -> VideoTask:
        task = await self.repo.get_or_404(task_id, "任务不存在")
        if actor and not self._can_access(task, actor):
            raise Forbidden("无权查看他人的任务")
        return task

    @staticmethod
    def _can_access(task: VideoTask, actor: CurrentUser) -> bool:
        """跨用户可见性只认 `task:read:all`，不认角色名。

        角色名写死会让「运营」「审核」等新角色永远绕不过 403，
        也让权限点目录与实际判定脱节。
        """
        return task.author_id == actor.id or actor.has(PERM_READ_ALL)

    # ------------------------------------------------------------------ 命令
    async def create_task(self, actor: CurrentUser, payload: CreateTaskIn) -> VideoTask:
        """创建任务：校验参数 → 落库（queued）→ 交给生成框架。

        请求线程不做任何模型调用，避免长耗时阻塞；前端拿到 queued 后靠轮询推进。
        模型与参数在这里就校验，非法请求**当场 400**，而不是异步失败后才暴露。
        """
        spec = resolve_model(self.router, payload.model, capability=Capability.VIDEO)
        duration_sec = resolve_duration(spec, payload.duration_sec)
        aspect_ratio = resolve_aspect_ratio(spec, AspectRatio(payload.aspect_ratio).value)

        # 参考素材在受理期就过一遍资产库：存在 / 可读 / 在库 / 类型受支持。
        # 不做这一步的话，前端传一个别人的私有资产 id 也会被原样落库——
        # 任务详情页能把 id 变成图，等于靠「猜 id」绕过了共享审批。
        # 现在这批 id 会真的交给驱动（`VideoTaskStore.load` 换成 storage key），
        # 所以「模型声明 0 张」必须当场拒：收下再静默丢掉，用户只会拿到一段
        # 与参考素材毫无关系的成片，界面上却没有任何异常。
        if payload.reference_library_ids and spec.max_references <= 0:
            raise BadRequest(
                f"模型 {spec.label} 不支持参考素材，请换用支持参考素材的模型"
            )
        references = await resolve_reference_images(
            self.session,
            actor,
            payload.reference_library_ids,
            limit=spec.max_references,
            # 视频替换模型吃「原片视频 + 参考图 + 音频」混合输入；纯文生视频
            # 模型在上面已经被 0 张上限挡掉了。
            kinds=(
                LibraryAssetKind.IMAGE,
                LibraryAssetKind.VIDEO,
                LibraryAssetKind.AUDIO,
            ),
        )

        # 文本快照在受理期冻结，与生图同一口径：提交 / 重试只读这份，
        # 资产改稿不会让同一条任务的两跳用上不同的提示词。
        text_refs = await freeze_text_refs(
            self.session,
            actor,
            payload.text_library_ids,
            prompt=payload.prompt,
        )

        title = (payload.title or payload.prompt).strip()[:200]
        task = VideoTask(
            title=title or DEFAULT_TITLE,
            prompt=payload.prompt.strip(),
            status=TaskStatus.QUEUED.value,
            # `model` 存可读名（列表/详情直接展示、关键词搜索也按它），
            # `model_id` 存目录 id（聚合、与 /models 对齐、路由供应商都用它）
            model=spec.label,
            model_id=spec.id,
            duration_sec=duration_sec,
            aspect_ratio=aspect_ratio,
            progress=0,
            style_preset=payload.style_preset,
            # 存去重保序之后的 id：同一张图传两遍不会多占一个保护名额
            reference_library_ids=[ref.id for ref in references],
            text_json=dumps_text_refs(text_refs),
            # 封面由真实结果决定：**不写占位图**。以前这里塞一张 picsum 风景照，
            # 生成完又没人改它（供应商不回首帧），任务中心一直显示那张假图 ——
            # 用户看到的是「生成结果没出来」，其实成片早就有了。
            # 空串让前端走渐变兜底；成片入库后由 `attach_cover` 写回真实首帧。
            cover_url="",
            author_id=actor.id,
            author_name=actor.name,
        )
        await self.repo.add(task)
        await self.session.commit()

        # 一行完成「入队 + 幂等键 + 超时 + 后续轮询编排」
        await self._dispatch(task)
        logger.info("任务已创建并入队 id=%s author=%s model=%s", task.id, actor.id, spec.id)
        return task

    async def retry_task(self, task_id: str, actor: CurrentUser) -> VideoTask:
        # 这里刻意**不走** `get_task(task_id, actor)`：`read:all` 与 `retry:any`
        # 是两个独立开关，谁都不蕴含谁。先过可见性会让「只有 retry:any 的客服」永远
        # 403（权限点勾了没反应），而只有 `read:all` 的运营也不该顺带获得「替人花钱」
        # 的能力（R4-06 / R5-05）。
        task = await self.repo.get_or_404(task_id, "任务不存在")
        if task.author_id != actor.id and not actor.has(PERM_RETRY_ANY):
            raise Forbidden("无权重试他人的任务")
        if TaskStatus(task.status) is not TaskStatus.FAILED:
            raise InvalidState("仅失败的任务可以重试")

        task.status = TaskStatus.QUEUED.value
        task.progress = 0
        task.error_message = None
        task.external_id = None
        # 人工重试 = 用户明确要求「再来一次」：清掉旧的提交意图标记，
        # 否则「attempts>0 且无 external_id」会被误判为「提交结果未知」，
        # 重试刚入队就被防重复计费的防线拦下（R3-01）
        task.submit_attempts = 0
        task.output_url = None
        task.downloadable = False
        # 封面也是上一版产物的：不清掉，重试期间卡片会继续显示旧片的首帧，
        # 用户以为「点了重试没反应」。新片入库后由 `attach_cover` 再写回来。
        task.cover_url = ""
        task.finished_at = None
        task.updated_at = datetime.now(tz=UTC)
        await self.session.commit()

        await self._dispatch(task)
        return task

    async def toggle_favorite(self, task_id: str, actor: CurrentUser) -> VideoTask:
        """收藏切换用单语句完成，避免「读-改-写」在并发下丢更新。"""
        task = await self.get_task(task_id, actor)
        await self.session.execute(
            update(VideoTask)
            .where(VideoTask.id == task.id)
            .values(favorited=~VideoTask.favorited)
        )
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def update_task(
        self, task_id: str, actor: CurrentUser, *, title: str | None, favorited: bool | None
    ) -> VideoTask:
        task = await self.get_task(task_id, actor)
        if title is not None:
            # 空标题是合法输入（用户清空），落成占位名，而不是静默忽略
            task.title = title.strip()[:200] or DEFAULT_TITLE
        if favorited is not None:
            task.favorited = favorited
        await self.session.commit()
        return task

    async def delete_tasks(
        self, ids: list[str], actor: CurrentUser
    ) -> tuple[int, list[str]]:
        """批量删除，返回 (删除条数, 被跳过的 id)。

        跳过有三种原因：id 根本不存在、属于别人且调用者没有 `task:read:all`、
        或者（极端并发）刚被别人删掉。**不再静默**：前端要把 skipped 显示出来，
        否则用户会以为是自己少选了。详情接口对无权限是 403，这里不整批 403 是
        因为批量场景下一条越权不该让其余十条白删——但必须让调用方看得见。
        """
        tasks = await self.repo.get_many(ids)
        owned = [t for t in tasks if self._can_access(t, actor)]
        owned_ids = {task.id for task in owned}
        # 「查不到」与「没权限」都落在这里：对外都是「这条没删成」，
        # 具体原因在服务端日志里区分（不向无权限者泄漏「这个 id 存在」）
        skipped = [task_id for task_id in ids if task_id not in owned_ids]
        for task in owned:
            await self.session.delete(task)
        await self.session.commit()
        if skipped:
            logger.warning(
                "批量删除跳过 %d 条（不存在或无权限）: %s", len(skipped), skipped[:20]
            )
        return len(owned), skipped

    # ------------------------------------------------------------------ 内部
    def to_out(self, task: VideoTask) -> TaskOut:
        """ORM → 出参，并补齐 `modelId`。

        历史数据（以及早期前端）里 `model` 存的是可读名，`model_id` 为空；
        这里用目录反查补齐，接口层面永远是「label + id」成对出现，
        调用方不需要知道库里存的是哪一种形态。
        """
        out = TaskOut.model_validate(task)
        if not out.model_id:
            spec = self.router.spec_for(task.model)
            out.model_id = spec.id if spec else None
        return out

    async def present(self, task: VideoTask) -> TaskOut:
        """出参：`to_out` + 把封面 key 现签成地址。

        「库里存 key、出参才变地址」是 `infra.artifacts` 定下的口径（换存储驱动
        不裂图）。`feed` 一直在做这件事，详情 / 列表漏了：前端拿
        `library/previews/xx.jpg` 当 URL 用，封面直接 404 —— 表现为任务卡空白、
        预览弹窗的海报是一块黑。
        """
        out = self.to_out(task)
        if out.cover_url:
            try:
                out.cover_url = (await artifact_urls([out.cover_url]))[0] or out.cover_url
            except Exception:  # noqa: BLE001 - 地址算不出来不该让详情接口 500
                logger.exception("任务封面地址转换失败 id=%s", task.id)
        return out

    async def present_many(self, tasks: Sequence[VideoTask]) -> list[TaskOut]:
        """批量版 `present`：一次签名，避免列表页 24 条各签一次。"""
        outs = [self.to_out(task) for task in tasks]
        keys = [out.cover_url for out in outs]
        if not any(keys):
            return outs
        try:
            urls = await artifact_urls(keys)
        except Exception:  # noqa: BLE001 - 同上：签不出来就留 key，前端兜底显示占位
            logger.exception("任务封面地址批量转换失败 count=%d", len(outs))
            return outs
        for out, url in zip(outs, urls, strict=False):
            if url:
                out.cover_url = url
        return outs

    def to_retry_out(self, task: VideoTask) -> TaskRetryOut:
        """重试的出参：**最小投影**。

        `retry_task` 只能按 id 取行（不能先过可见性，见方法注释），
        所以这里绝不能返回整份 `TaskOut`——否则「能重试」顺带成了
        「能读任意失败任务的 prompt 与作者」（R6-05）。
        """
        return TaskRetryOut(
            id=task.id, status=TaskStatus(task.status), updated_at=task.updated_at
        )

    async def _dispatch(self, task: VideoTask) -> None:
        """入队失败必须让任务显式变为 failed。

        「已提交事务 + 入队失败」如果不处理，任务会永久停在 queued，
        用户只能看到一直排队中，也没有重试入口。
        """
        try:
            await get_runner(self.queue).dispatch(KIND_VIDEO, task.id)
        except Exception:  # noqa: BLE001
            logger.exception("任务入队失败 id=%s", task.id)
            task.status = TaskStatus.FAILED.value
            task.error_message = "任务调度失败，请重试"
            task.finished_at = datetime.now(tz=UTC)
            await self.session.commit()
            raise UpstreamError("任务调度失败，请稍后重试") from None

__all__ = ["KIND_VIDEO", "TaskService"]
