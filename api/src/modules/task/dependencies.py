"""任务模块的依赖装配。

权限依赖也在这里定义 —— **权限点属于模块自己**，核心不登记、不感知：

    actor: TaskCreateDep   # 等价于 Depends(require("task:create"))

这样加一个权限点不需要改 `api/deps.py`。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from api.deps import QueueDep, SessionDep, require
from core.context import CurrentUser
from infra import get_model_router
from infra.ai.router import ModelRouter
from modules.task.permissions import (
    PERM_CREATE,
    PERM_DELETE,
    PERM_READ,
    PERM_UPDATE,
)
from modules.task.repository import TaskRepository
from modules.task.service import TaskService


def get_task_repository(session: SessionDep) -> TaskRepository:
    return TaskRepository(session)


TaskRepoDep = Annotated[TaskRepository, Depends(get_task_repository)]


def get_task_service(
    session: SessionDep,
    repo: TaskRepoDep,
    queue: QueueDep,
    router: Annotated[ModelRouter, Depends(get_model_router)],
) -> TaskService:
    return TaskService(session=session, repo=repo, queue=queue, router=router)


TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]

# ---------- 权限依赖 ----------
# 权限码只写在 permissions.py 一处，这里全部引常量：
# 改常量即全模块生效，不会出现「常量改了、路由还挂着旧字符串」的分叉
TaskReadDep = Annotated[CurrentUser, Depends(require(PERM_READ))]
TaskCreateDep = Annotated[CurrentUser, Depends(require(PERM_CREATE))]
TaskUpdateDep = Annotated[CurrentUser, Depends(require(PERM_UPDATE))]
TaskDeleteDep = Annotated[CurrentUser, Depends(require(PERM_DELETE))]
