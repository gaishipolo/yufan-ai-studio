"""任务领域服务：业务规则都在这一层，路由只做协议转换。

事务边界明确：一次 service 方法 = 一次提交。

注意这里的 service **不认识供应商、不认识队列协议细节**：
创建任务只调用 `runner.dispatch()`，提交/轮询/重试/事件都由生成框架负责。
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import Forbidden, InvalidState, UpstreamError
from core.logging import get_logger
from core.response import PageParams
from generation.runner import get_runner
from infra.ai.base import Capability
from infra.ai.router import ModelRouter
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

        items = [self.to_out(row) for row in rows]
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
            reference_asset_ids=list(payload.reference_asset_ids),
            cover_url=self._placeholder_cover(),
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

    @staticmethod
    def _placeholder_cover() -> str:
        """占位封面：真实实现由供应商返回首帧后写回。

        种子用随机值而不是 prompt 哈希——占位图会给到第三方图片站，
        没必要把用户的画面描述（哪怕只是摘要）带出去。
        """
        return f"https://picsum.photos/seed/{secrets.token_hex(4)}/720/1280"


__all__ = ["KIND_VIDEO", "TaskService"]
