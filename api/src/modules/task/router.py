"""任务模块路由：只做 HTTP 协议转换，业务逻辑在 service。

路径与前端 `entities/task/api/task.api.ts` 严格对齐：
GET    /tasks/feed           统一任务中心（视频 / 生图 / 替换档案）
GET    /tasks                列表（含 stats）
GET    /tasks/stats          仅统计
GET    /tasks/{id}           详情
POST   /tasks                创建
PATCH  /tasks/{id}           局部更新
POST   /tasks/{id}/retry     重试
POST   /tasks/{id}/favorite  收藏切换
DELETE /tasks                批量删除（body: {ids}）

鉴权：每个接口声明自己需要的权限码，权限点由本模块在 `ModuleSpec` 里登记。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from api.deps import PageDep, rate_limit
from core.config import settings
from core.response import ApiResponse
from modules.task.dependencies import (
    TaskCreateDep,
    TaskDeleteDep,
    TaskReadDep,
    TaskServiceDep,
    TaskUpdateDep,
)
from modules.task.enums import TaskScope, TaskSort, TaskStatusFilter
from modules.task.feed_schema import FeedSource, FeedStatus, TaskFeedOut
from modules.task.feed_service import list_feed
from modules.task.schema import (
    CreateTaskIn,
    DeleteTasksIn,
    DeleteTasksOut,
    TaskListOut,
    TaskListQuery,
    TaskOut,
    TaskRetryOut,
    TaskStats,
    UpdateTaskIn,
)

router = APIRouter(prefix="/tasks", tags=["任务"])

TaskId = Annotated[str, Path(description="任务 ID")]


@router.get("/feed", summary="统一任务中心：视频生成、生图与本人可见的替换档案")
async def task_feed(
    service: TaskServiceDep,
    actor: TaskReadDep,
    page: PageDep,
    source: FeedSource = "all",
    status: FeedStatus = "all",
    keyword: Annotated[str, Query(max_length=100)] = "",
    sort: TaskSort = TaskSort.CREATED_DESC,
    scope: TaskScope = TaskScope.MINE,
    only_authorized: Annotated[bool, Query(alias="onlyAuthorized")] = False,
    stats_only: Annotated[
        bool,
        Query(alias="statsOnly", description="只返回统计，不组装条目；角标等轮询场景用"),
    ] = False,
) -> ApiResponse[TaskFeedOut]:
    query = TaskListQuery(keyword=keyword, sort=sort, scope=scope,
                          only_authorized=only_authorized)
    return ApiResponse.ok(
        await list_feed(service, actor, query, page, source, status, stats_only)
    )


@router.get("", summary="任务列表")
async def list_tasks(
    service: TaskServiceDep,
    actor: TaskReadDep,
    page: PageDep,
    task_status: Annotated[
        TaskStatusFilter, Query(alias="status", description="状态筛选")
    ] = TaskStatusFilter.ALL,
    keyword: Annotated[str, Query(max_length=100, description="标题 / 模型 / ID 模糊搜索")] = "",
    sort: Annotated[TaskSort, Query(description="排序")] = TaskSort.CREATED_DESC,
    scope: Annotated[TaskScope, Query(description="数据范围")] = TaskScope.MINE,
    only_authorized: Annotated[
        bool, Query(alias="onlyAuthorized", description="仅显示可下载任务")
    ] = False,
) -> ApiResponse[TaskListOut]:
    query = TaskListQuery(
        status=task_status,
        keyword=keyword,
        sort=sort,
        scope=scope,
        only_authorized=only_authorized,
    )
    return ApiResponse.ok(await service.list_tasks(actor, query, page))


@router.get("/stats", summary="任务统计（侧边栏角标）")
async def task_stats(
    service: TaskServiceDep,
    actor: TaskReadDep,
    scope: Annotated[TaskScope, Query(description="统计口径，与列表 scope 一致")] = TaskScope.MINE,
) -> ApiResponse[TaskStats]:
    return ApiResponse.ok(await service.stats(actor, scope))


@router.get("/{task_id}", summary="任务详情")
async def get_task(
    service: TaskServiceDep, actor: TaskReadDep, task_id: TaskId
) -> ApiResponse[TaskOut]:
    task = await service.get_task(task_id, actor)
    return ApiResponse.ok(service.to_out(task))


@router.post("", status_code=status.HTTP_201_CREATED, summary="创建任务")
async def create_task(
    service: TaskServiceDep,
    actor: TaskCreateDep,
    payload: CreateTaskIn,
    # 建任务是唯一会真花钱的入口（提交供应商），按用户限速。
    # 这是全项目第一个用 `rate_limit` 的地方，其余接口照抄这一行即可。
    _: None = Depends(rate_limit("task.create", limit=settings.rate_limit_default_per_min)),
) -> ApiResponse[TaskOut]:
    task = await service.create_task(actor, payload)
    return ApiResponse.ok(service.to_out(task), message="任务已创建")


@router.patch("/{task_id}", summary="更新任务（标题 / 收藏）")
async def update_task(
    service: TaskServiceDep, actor: TaskUpdateDep, task_id: TaskId, payload: UpdateTaskIn
) -> ApiResponse[TaskOut]:
    task = await service.update_task(
        task_id, actor, title=payload.title, favorited=payload.favorited
    )
    return ApiResponse.ok(service.to_out(task))


@router.post("/{task_id}/retry", summary="重试任务")
async def retry_task(
    service: TaskServiceDep,
    actor: TaskUpdateDep,
    task_id: TaskId,
    # 重试 = 重新提交供应商，和建任务一样会真花钱，额度对齐同一档
    _: None = Depends(rate_limit("task.retry", limit=settings.rate_limit_default_per_min)),
) -> ApiResponse[TaskRetryOut]:
    """重试的结果只回 `id` / `status` / `updatedAt`。

    不返回整份任务详情：`task:retry:any` 允许「帮别人重跑」，但没允许
    「读别人的 prompt」——那是 `task:read:all` 的事（R6-05）。
    """
    task = await service.retry_task(task_id, actor)
    return ApiResponse.ok(service.to_retry_out(task), message="已重新排队")


@router.post("/{task_id}/favorite", summary="收藏 / 取消收藏")
async def toggle_favorite(
    service: TaskServiceDep, actor: TaskUpdateDep, task_id: TaskId
) -> ApiResponse[TaskOut]:
    task = await service.toggle_favorite(task_id, actor)
    return ApiResponse.ok(service.to_out(task))


@router.delete("", summary="批量删除任务")
async def delete_tasks(
    service: TaskServiceDep,
    actor: TaskDeleteDep,
    payload: DeleteTasksIn,
    # 一次可以点很多条，单请求的写放大没有上限；额度给得比创建宽，但要有
    _: None = Depends(rate_limit("task.delete", limit=settings.rate_limit_default_per_min)),
) -> ApiResponse[DeleteTasksOut]:
    deleted, skipped = await service.delete_tasks(payload.ids, actor)
    return ApiResponse.ok(
        DeleteTasksOut(deleted=deleted, skipped=skipped),
        message=f"已删除 {deleted} 个任务"
        + (f"，{len(skipped)} 个未删除" if skipped else ""),
    )
