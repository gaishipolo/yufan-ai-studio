"""模板领域服务：业务规则都在这里，路由只做协议转换。

约定：一个 service 方法 = 一次事务提交（`await self.session.commit()`）。

## 开箱可用的横向能力（直接 import，不要自己 new）

| 能力 | 入口 | 注意 |
|---|---|---|
| 缓存 | `from infra import get_cache` | 只放可丢失的数据（限流计数、热点只读） |
| 出站 HTTP | `from infra.http import get_http, fetch_bytes` | 过 SSRF 校验、限大小、只重试幂等码 |
| 队列 | `from infra import get_queue` + `Job(name=..., payload=...)` | 只拿协议，换驱动业务不改 |
| 可靠事件 | `from db.outbox import record` | **与业务行同一次 commit**，不要单独提交 |
| 限流 | 路由上 `Depends(rate_limit(...))`，额度用配置默认值 | 只在显式挂载的接口生效 |
| 功能开关 | `from core.flags import is_on` | 名字用 `领域.动作`，doctor 会列出全部 |
| 指标 | `from core.metrics import METRICS` | 导入期声明一次；标签只用低基数字段 |
| 上下文 | `from core.context import get_current_user, get_request_id` | 日志已自动带 request id |
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import NotImplementedFeature
from core.response import Page, PageParams
from db.repository import BaseRepository
from modules.template.models import Template
from modules.template.schema import TemplateOut


class TemplateService:
    def __init__(self, session: AsyncSession, repo: BaseRepository[Template]) -> None:
        self.session = session
        self.repo = repo

    async def list_items(self, actor: CurrentUser, page: PageParams) -> Page[TemplateOut]:
        # TODO: 换成真实查询（参考 modules/task/repository.py 的过滤 + 分页写法）
        #   排序键必须以主键收尾；total 走 count(*)，不要 `list()` 再 `len()`
        raise NotImplementedFeature("模板列表待实现")
