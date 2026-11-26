"""模型驱动模块管理面：驱动清单、模型目录、任务提交/查询/取消/重试。

「下游其他模块调用生成」不经过这里，走 `core/ports/model_generation` 注入口；
这组 REST 是给运维与页面看状态用的，以及承载「提交结果未知」的人工重试出口。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from starlette.concurrency import run_in_threadpool

from api.deps import rate_limit, require
from core.audit import record as audit
from core.config import ENV_FILE, settings
from core.context import CurrentUser
from core.exceptions import BadRequest, NotFound
from core.ports.model_generation import GenerationTicket, SubmitSpec
from core.response import ApiResponse
from modules.model_driver.registry import (
    driver_summaries,
    load_errors,
)
from modules.model_driver.registry import (
    refresh as refresh_registry,
)
from modules.model_driver.runtime_config import apply_updates, list_items
from modules.model_driver.schema import (
    ConfigItemOut,
    ConfigListOut,
    ConfigUpdateIn,
    ConfigUpdateOut,
    DriverListOut,
    DriverOut,
    ModelListOut,
    ModelOut,
    RefreshOut,
    SubmitIn,
    TaskOut,
)
from modules.model_driver.service import ModelGenerationFacade

router = APIRouter(prefix="/model-driver", tags=["模型驱动"])


def get_facade() -> ModelGenerationFacade:
    return ModelGenerationFacade()


FacadeDep = Annotated[ModelGenerationFacade, Depends(get_facade)]
DriverReadDep = Annotated[CurrentUser, Depends(require("model_driver:read"))]
DriverCreateDep = Annotated[CurrentUser, Depends(require("model_driver:create"))]
DriverCancelDep = Annotated[CurrentUser, Depends(require("model_driver:cancel"))]
DriverRefreshDep = Annotated[CurrentUser, Depends(require("model_driver:refresh"))]
ConfigReadDep = Annotated[CurrentUser, Depends(require("model_driver:config:read"))]
ConfigWriteDep = Annotated[CurrentUser, Depends(require("model_driver:config:write"))]


def _task_out(ticket: GenerationTicket) -> TaskOut:
    return TaskOut(
        id=ticket.task_id,
        model=ticket.model,
        capability=ticket.capability,
        status=ticket.status,
        progress=ticket.progress,
        output_url=ticket.output_url,
        cover_url=ticket.cover_url,
        error=ticket.error,
        external_id=ticket.external_id,
        owner_id=ticket.owner_id,
        source=ticket.source,
        submit_attempts=ticket.submit_attempts,
        created_at=ticket.created_at,
    )


@router.get("/drivers", summary="已自动载入的模型驱动清单")
async def list_drivers(actor: DriverReadDep) -> ApiResponse[DriverListOut]:
    infos = driver_summaries()
    items = [
        DriverOut(
            name=item.name,
            label=item.label,
            version=item.version,
            path=item.path,
            capabilities=list(item.capabilities),
            model_count=item.model_count,
            supports_poll=item.supports_poll,
            loaded=item.loaded,
            error=item.error,
        )
        for item in infos
    ]
    return ApiResponse.ok(DriverListOut(items=items, total=len(items), errors=list(load_errors())))


@router.get("/models", summary="统一生成接口可用的模型目录")
async def list_models(
    facade: FacadeDep,
    actor: DriverReadDep,
    capability: Annotated[str, Query(description="能力筛选：video / image / audio")] = "",
) -> ApiResponse[ModelListOut]:
    items = [
        ModelOut(
            id=model.id,
            label=model.label,
            capability=model.capability,
            provider=model.provider,
            description=model.description,
            param_schema=dict(model.param_schema),
            max_references=model.max_references,
            replacement=model.replacement,
        )
        for model in facade.models(capability)
    ]
    return ApiResponse.ok(ModelListOut(items=items, total=len(items)))


@router.post("/tasks", summary="提交一次统一生成任务")
async def submit_task(
    payload: SubmitIn,
    facade: FacadeDep,
    actor: DriverCreateDep,
    _: None = Depends(rate_limit("model_driver.submit", limit=settings.rate_limit_default_per_min)),
) -> ApiResponse[TaskOut]:
    ticket = await facade.submit(
        SubmitSpec(
            model=payload.model,
            prompt=payload.prompt,
            owner_id=actor.id,
            params=payload.params,
            reference_urls=tuple(payload.reference_urls),
            name=payload.name,
            source=payload.source,
        )
    )
    return ApiResponse.ok(_task_out(ticket))


@router.get("/tasks/{task_id}", summary="查询统一生成任务")
async def get_task(
    task_id: str, facade: FacadeDep, actor: DriverReadDep
) -> ApiResponse[TaskOut]:
    ticket = await facade.query(task_id, actor)
    if ticket is None:
        raise NotFound("生成任务不存在")
    return ApiResponse.ok(_task_out(ticket))


@router.post("/tasks/{task_id}/cancel", summary="取消生成任务")
async def cancel_task(
    task_id: str,
    facade: FacadeDep,
    actor: DriverCancelDep,
    _: None = Depends(rate_limit("model_driver.cancel", limit=settings.rate_limit_default_per_min)),
) -> ApiResponse[TaskOut]:
    ticket = await facade.cancel(task_id, actor)
    if ticket is None:
        raise NotFound("生成任务不存在")
    return ApiResponse.ok(_task_out(ticket))


@router.post("/tasks/{task_id}/retry", summary="人工重试失败的生成任务")
async def retry_task(
    task_id: str,
    facade: FacadeDep,
    actor: DriverCreateDep,
    confirm_charged: Annotated[
        bool, Query(alias="confirmCharged", description="确认供应商可能已计费")
    ] = False,
    _: None = Depends(rate_limit("model_driver.retry", limit=settings.rate_limit_default_per_min)),
) -> ApiResponse[TaskOut]:
    ticket = await facade.retry(task_id, actor, confirm_charged=confirm_charged)
    return ApiResponse.ok(_task_out(ticket))


@router.post("/refresh", summary="刷新驱动目录，热接上新增的驱动文件")
async def refresh_drivers(
    actor: DriverRefreshDep,
    _: None = Depends(
        rate_limit("model_driver.refresh", limit=settings.rate_limit_default_per_min)
    ),
) -> ApiResponse[RefreshOut]:
    """重新扫描驱动目录，把**新增**的驱动文件热接上。

    - **进程级**：只影响本实例。多实例部署时其他实例靠各自的周期刷新收敛。
    - **改已加载的文件不热替换**（reload 会丢正在跑的实例状态），要重启才生效 ——
      返回结果里的 `changedFiles` 就是这条提醒。
    - 刷新会**连带重跑**必填项与提交预算预检：配置被改坏要在这里就发现，
      而不是等下次重启。
    """
    result = await run_in_threadpool(refresh_registry, operator=actor.id)
    return ApiResponse.ok(
        RefreshOut(
            added=list(result.added),
            kept=list(result.kept),
            changed_files=list(result.changed_files),
            missing_handlers=list(result.missing_handlers),
            errors=list(result.errors),
            operator=result.operator,
            refreshed_at=result.refreshed_at,
        )
    )


@router.get("/config", summary="运行时配置清单（密钥脱敏）")
async def get_runtime_config(actor: ConfigReadDep) -> ApiResponse[ConfigListOut]:
    """列出运维页可编辑的配置项。

    密钥字段的 `value` 恒为空，只有 `configured` 与 8 位指纹；
    `source=environment` 的项由进程环境变量注入，改文件不生效，页面只读。
    """
    items = list_items()
    return ApiResponse.ok(
        ConfigListOut(
            items=[
                ConfigItemOut(
                    key=item.key,
                    label=item.label,
                    group=item.group,
                    secret=item.secret,
                    value=item.value,
                    configured=item.configured,
                    fingerprint=item.fingerprint,
                    masked=item.masked,
                    source=item.source,
                    editable=item.editable,
                    hint=item.hint,
                    required=item.required,
                )
                for item in items
            ],
            env_file=ENV_FILE,
            groups=list(dict.fromkeys(item.group for item in items)),
        )
    )


@router.put("/config", summary="修改运行时配置，下一次请求即生效")
async def put_runtime_config(
    payload: ConfigUpdateIn,
    actor: ConfigWriteDep,
    _: None = Depends(
        rate_limit("model_driver.config.write", limit=settings.rate_limit_default_per_min)
    ),
) -> ApiResponse[ConfigUpdateOut]:
    """写回服务实际读取的 `.env`，并清掉配置缓存使其立即生效。

    `values` 里只提交要改的键；密钥空串表示「不改」（要清空请走改文件）。
    只接受声明清单里的键，白名单值做 host 校验，避免把 `.env` 写成 SSRF 后门。
    """
    try:
        result = await run_in_threadpool(apply_updates, dict(payload.values))
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    # 审计只记键名，不记值 —— 白名单值可回显，密钥值写进去就收不回来
    if result.updated:
        await audit(
            "model_driver.config.update",
            actor_id=actor.id,
            target_type="runtime_config",
            target_id="env",
            detail={"keys": list(result.updated), "skipped": list(result.skipped)},
        )
    return ApiResponse.ok(
        ConfigUpdateOut(
            updated=list(result.updated),
            unchanged=list(result.unchanged),
            skipped=list(result.skipped),
        )
    )
