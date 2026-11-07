"""脚手架模板。

用 `string.Template`（`$var` / `${var}`）而不是 `str.format`，
因为模板里大量出现 Python 的 `{}`，format 会解析失败。
"""

from __future__ import annotations

from string import Template

INIT_TMPL = Template(
    '''"""${label}模块。

本文件是模块对外的唯一契约：路由 + 权限点。
`api.discovery` 扫描到 `module` 对象即自动挂载，无需修改核心代码。
"""

from api.module import ModuleSpec
from core.permissions import PermissionSpec
from modules.${name}.router import router
${generation_import}

module = ModuleSpec(
    name="${name}",
    router=router,
    tags=["${label}"],
    description="${label}",
    permissions=(
        PermissionSpec("${perm}:read", "查看${label}", group="${group}"),
        PermissionSpec("${perm}:create", "创建${label}", group="${group}"),
        PermissionSpec("${perm}:update", "编辑${label}", group="${group}"),
        PermissionSpec("${perm}:delete", "删除${label}", group="${group}"),
    ),
)
'''
)

ERRORS_TMPL = Template(
    '''"""${label}错误码（slot ${slot}，见 `docs/EXTENDING.md` §7 的号段分配表）。

编号规则：`code = HTTP状态码 × 100 + ${slot} × 10 + seq`，
因此 `code // 100` 恒等于 HTTP 状态码。每个 (状态码, seq) 在一个模块内只能用一次，
冲突会在导入期由 `ModuleErrors` 直接报错，不会静默覆盖。
"""

from __future__ import annotations

from core.exceptions import AppError, ModuleErrors

errors = ModuleErrors("${name}", slot=${slot})


class ${Name}Busy(AppError):
    """示例错误码：删掉它，换成你真正需要的（每个状态码最多 9 个）。"""

    code = errors.code(409, 1)
    message = "${label}正在处理中，请稍后再试"
    http_status = 409
'''
)

MODELS_TMPL = Template(
    '''"""${label}ORM 实体。

表名必须加前缀或复数，避免与其他模块撞名。
新增字段后执行：alembic revision --autogenerate -m "${name} init"
"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDMixin


class ${Name}(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "${table}"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="queued", nullable=False, index=True
    )
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
'''
)

SCHEMA_TMPL = Template(
    '''"""${label}出入参。字段用 snake_case 书写，序列化自动转 camelCase。"""

from __future__ import annotations

from core.schema import CamelModel, UtcDateTime


class ${Name}Out(CamelModel):
    id: str
    title: str
    status: str = "queued"
    owner_id: str
    created_at: UtcDateTime | None = None


class Create${Name}In(CamelModel):
    title: str
    prompt: str = ""


class Update${Name}In(CamelModel):
    title: str | None = None
'''
)

SERVICE_TMPL = Template(
    '''"""${label}领域服务：业务规则都在这里，路由只做协议转换。

约定：一个 service 方法 = 一次事务提交（`await self.session.commit()`）。

## 开箱可用的横向能力（直接 import，不要自己 new）

| 能力 | 入口 | 注意 |
|---|---|---|
| 缓存 | `from infra import get_cache` | 只放可丢失的数据（限流计数、热点只读） |
| 出站 HTTP | `from infra.http import get_http, fetch_bytes` | 过 SSRF 校验、限大小、只重试幂等码 |
| 队列 | `from infra import get_queue` + `Job(name=..., payload=...)` | 只拿协议，换驱动业务不改 |
| 可靠事件 | `from db.outbox import record` | **与业务行同一次 commit**，不要单独提交 |
| 限流 | 路由上 `Depends(rate_limit("${name}.create", limit=30))` | 只在显式挂载的接口生效 |
| 功能开关 | `from core.flags import is_on` | 名字用 `领域.动作`，doctor 会列出全部 |
| 指标 | `from core.metrics import METRICS` | 导入期声明一次；标签只用低基数字段 |
| 上下文 | `from core.context import get_current_user, get_request_id` | 日志已自动带 request id |
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import NotImplementedFeature
from core.response import PageParams
from db.repository import BaseRepository
from modules.${name}.models import ${Name}
from modules.${name}.schema import ${Name}Out


class ${Name}Service:
    def __init__(self, session: AsyncSession, repo: BaseRepository[${Name}]) -> None:
        self.session = session
        self.repo = repo

    async def list_items(self, actor: CurrentUser, page: PageParams) -> list[${Name}Out]:
        # TODO: 换成真实查询（参考 modules/task/repository.py 的过滤 + 分页写法）
        raise NotImplementedFeature("${label}列表待实现")
'''
)

DEPENDENCIES_TMPL = Template(
    '''"""${label}依赖装配。

权限依赖写在模块内部：新增权限点不需要修改 `api/deps.py`。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from api.deps import SessionDep, require
from core.context import CurrentUser
from db.repository import BaseRepository
from modules.${name}.models import ${Name}
from modules.${name}.service import ${Name}Service


def get_${name}_repository(session: SessionDep) -> BaseRepository[${Name}]:
    return BaseRepository(session, ${Name})


${Name}RepoDep = Annotated[BaseRepository[${Name}], Depends(get_${name}_repository)]


def get_${name}_service(
    session: SessionDep, repo: ${Name}RepoDep
) -> ${Name}Service:
    return ${Name}Service(session=session, repo=repo)


${Name}ServiceDep = Annotated[${Name}Service, Depends(get_${name}_service)]

# ---------- 权限依赖 ----------
${Name}ReadDep = Annotated[CurrentUser, Depends(require("${perm}:read"))]
${Name}CreateDep = Annotated[CurrentUser, Depends(require("${perm}:create"))]
${Name}UpdateDep = Annotated[CurrentUser, Depends(require("${perm}:update"))]
${Name}DeleteDep = Annotated[CurrentUser, Depends(require("${perm}:delete"))]
'''
)

ROUTER_TMPL = Template(
    '''"""${label}路由：只做协议转换。

注意：每个接口显式声明所需权限码，权限点在本模块 `__init__.py` 里登记，
核心不感知。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from api.deps import PageDep, rate_limit
from core.exceptions import NotImplementedFeature
from core.response import ApiResponse
from modules.${name}.dependencies import (
    ${Name}CreateDep,
    ${Name}ReadDep,
    ${Name}ServiceDep,
)
from modules.${name}.schema import Create${Name}In, ${Name}Out

router = APIRouter(prefix="/${name}", tags=["${label}"])


@router.get("", summary="${label}列表")
async def list_items(
    service: ${Name}ServiceDep, actor: ${Name}ReadDep, page: PageDep
) -> ApiResponse[list[${Name}Out]]:
    return ApiResponse.ok(await service.list_items(actor, page))


# 写接口默认挂限流：漏挂等于完全不限（限流是依赖级，没有全局兜底）
@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="创建${label}",
    dependencies=[Depends(rate_limit("${name}.create", limit=30))],
)
async def create_item(
    service: ${Name}ServiceDep, actor: ${Name}CreateDep, payload: Create${Name}In
) -> ApiResponse[${Name}Out]:
    # TODO: 调用 service 实现创建；若需要异步生成，用
    #       await get_runner(queue).dispatch("${name}", record.id)
    # 必须是 NotImplementedFeature（501），裸抛 NotImplementedError 会变成 500
    raise NotImplementedFeature("${label}创建待实现")
'''
)

GENERATION_TMPL = Template(
    '''"""${label}的生成流水线骨架。

框架会负责：提交、轮询、重试、超时、幂等、事件发布。
你要补的只有「表 ↔ GenerationRecord」的映射。

TODO：
1. 在 models.py 补 external_id / progress / output_url / error_message /
   finished_at / submit_attempts
2. 实现下面 7 个方法（照 modules/task/pipeline.py 抄；
   mark_submitted 记得把 submit_attempts 复位为 0）
3. 创建记录后调用：await get_runner(queue).dispatch("${name}", record.id)
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import NotImplementedFeature
from generation.pipeline import register_pipeline
from generation.spec import GenerationRecord
from infra.ai.base import Capability, GenerationRequest
from modules.${name}.models import ${Name}


class ${Name}Store:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load(self, record_id: str) -> GenerationRecord | None:
        record = await self.session.get(${Name}, record_id)
        if record is None:
            return None
        return GenerationRecord(
            id=record.id,
            kind="${name}",
            model="",
            prompt=record.title,
            status=record.status,
            owner_id=record.owner_id,
        )

    async def mark_submitting(self, record_id: str) -> None:
        # 调供应商**之前**落一次「提交意图」：进程崩在「已受理 → 写库」之间时，
        # 这是唯一能说明「可能已计费」的痕迹，没有它重启会重复提交
        raise NotImplementedFeature("TODO: submit_attempts += 1")

    async def clear_submit_attempt(self, record_id: str) -> None:
        # 拿到了明确异常，说明本次提交没成功，清掉意图以便安全重试
        raise NotImplementedFeature("TODO: submit_attempts 归零")

    async def mark_submitted(self, record_id: str, external_id: str) -> None:
        # TODO: 写入 external_id、置为 running，并把 submit_attempts 复位为 0
        # （不复位的话，此后任何失败后的重试都会被误判为「提交结果未知」）
        raise NotImplementedFeature("TODO: 写入 external_id 并置为 running")

    async def mark_progress(self, record_id: str, progress: int) -> None:
        raise NotImplementedFeature("TODO: 更新进度")

    async def mark_succeeded(
        self,
        record_id: str,
        *,
        output_url: str | None,
        cover_url: str | None,
        progress: int = 100,
    ) -> None:
        raise NotImplementedFeature("TODO: 置为终态并写入产物地址")

    async def mark_failed(self, record_id: str, error: str) -> None:
        raise NotImplementedFeature("TODO: 置为终态并写入失败原因")


@register_pipeline
class ${Name}GenerationPipeline:
    kind = "${name}"
    capability = Capability.VIDEO

    def store(self, session: AsyncSession) -> ${Name}Store:
        return ${Name}Store(session)

    async def build_request(self, record: GenerationRecord) -> GenerationRequest:
        return GenerationRequest(
            capability=self.capability,
            model=record.model,
            prompt=record.prompt,
            params=record.params,
            request_id=record.id,
        )
'''
)
