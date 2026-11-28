"""LLM 配置页 REST：供应商 CRUD + 拉取上游模型 + 连通测试。

只管**配置**（写 .env），不碰 `infra.llm` 的调用协议 —— 那是能力层的事。
拉取模型和连通测试会发真实外网请求，是部署验收动作，不在 doctor 里跑。
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Query
from starlette.concurrency import run_in_threadpool

from api.deps import SessionDep, rate_limit, require
from core.audit import record as audit
from core.config import settings
from core.context import CurrentUser
from core.exceptions import AppError, BadRequest
from core.response import ApiResponse
from infra.llm import LlmMessage, LlmRequest, OpenAICompatibleClient
from infra.llm.providers.openai_compatible import LLMSettings
from infra.llm.providers.source import normalize_provider_name, provider_config
from modules.llm_config.migration import rename_provider, sync_random_settings_after_rename
from modules.llm_config.schema import (
    FetchModelsIn,
    FetchModelsOut,
    LlmModelOut,
    LlmProviderOut,
    LlmProvidersOut,
    ModelChainIn,
    RenameProviderIn,
    SaveProviderIn,
    UpstreamModelOut,
)
from modules.llm_config.store import (
    ModelMapping,
    ProviderListOut,
    ProviderOut,
    _validate_base_url,
    _validate_value,
    credentials_of,
    delete_provider,
    list_providers,
    read_model_chain,
    save_model_chain,
    save_provider,
)

router = APIRouter(prefix="/llm-config", tags=["LLM 配置"])

ReadDep = Annotated[CurrentUser, Depends(require("llm_config:read"))]
WriteDep = Annotated[CurrentUser, Depends(require("llm_config:write"))]


def _select_model(provider: ProviderOut, model_id: str) -> ModelMapping:
    """选本次连通测试的模型。

    留空取第一个；显式传了但不在映射里是 4xx —— 静默回落到第一个会让
    调用方以为测的是自己点的那一款。
    """
    if provider.config_error:
        raise BadRequest(f"供应商 {provider.name} 的模型映射配置有误：{provider.config_error}")
    if not provider.models:
        raise BadRequest("该供应商还没有配置模型映射")
    wanted = model_id.strip()
    if not wanted:
        return provider.models[0]
    mapping = next((model for model in provider.models if model.id == wanted), None)
    if mapping is None:
        options = "、".join(model.id for model in provider.models)
        raise BadRequest(f"模型不在该供应商的映射里：{wanted}；可选：{options}")
    return mapping


def _fetch_target(
    saved: ProviderOut | None,
    saved_key: str,
    base_url: str,
    api_key: str,
) -> tuple[str, str]:
    """拉取模型用哪一套网关/密钥：草稿值优先，留空才用已保存的。

    新供应商还没有落盘配置，必须把网关与密钥一起带过来；编辑老供应商时
    改了网关也能先拉、再保存。草稿网关与保存走同一套 SSRF 校验，不因为
    「只是拉列表」就放宽。
    """
    draft_url = base_url.strip()
    draft_key = api_key.strip()
    if draft_url:
        try:
            draft_url = _validate_base_url(_validate_value("网关地址", draft_url))
            draft_key = _validate_value("密钥", draft_key)
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc
    url = draft_url or (saved.base_url if saved else "")
    key = draft_key or saved_key
    if not url or not key:
        raise BadRequest("请先填好网关地址和密钥，再拉取模型")
    return url, key


def _providers_out(result: ProviderListOut) -> LlmProvidersOut:
    try:
        chain = list(read_model_chain())
        chain_error = ""
    except ValueError as exc:
        # 链坏了只影响链本身：供应商列表照常返回，页面能继续编辑/修复。
        chain = []
        chain_error = str(exc)
    return LlmProvidersOut(
        providers=[_provider_out(item) for item in result.providers],
        model_chain=chain,
        model_chain_error=chain_error,
    )


def _provider_out(item) -> LlmProviderOut:
    return LlmProviderOut(
        name=item.name,
        base_url=item.base_url,
        api_key_configured=item.api_key_configured,
        api_key_masked=item.api_key_masked,
        api_key_fingerprint=item.api_key_fingerprint,
        models=[LlmModelOut(id=m.id, upstream=m.upstream) for m in item.models],
        source=item.source,
        config_error=item.config_error,
    )


@router.get("/providers", summary="列出 LLM 供应商配置（密钥脱敏）")
async def get_providers(actor: ReadDep) -> ApiResponse[LlmProvidersOut]:
    result = await run_in_threadpool(list_providers)
    return ApiResponse.ok(_providers_out(result))


@router.put("/providers", summary="新增或更新 LLM 供应商，下一次请求即生效")
async def put_provider(
    payload: SaveProviderIn,
    actor: WriteDep,
    _: None = Depends(rate_limit("llm_config.write", limit=settings.rate_limit_default_per_min)),
) -> ApiResponse[LlmProviderOut]:
    try:
        result = await run_in_threadpool(
            save_provider,
            payload.name,
            base_url=payload.base_url,
            api_key=payload.api_key,
            models=tuple(ModelMapping(m.id, m.upstream) for m in payload.models),
        )
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    await audit(
        "llm_config.provider.save",
        actor_id=actor.id,
        target_type="llm_provider",
        target_id=result.name,
        # 审计只记「改了哪家、几个模型、网关」，绝不记密钥
        detail={
            "models": len(result.models),
            "api_key_changed": bool(payload.api_key.strip()),
            "base_url": result.base_url,
        },
    )
    return ApiResponse.ok(_provider_out(result))


@router.delete("/providers/{name}", summary="删除 LLM 供应商配置")
async def remove_provider(
    name: str,
    actor: WriteDep,
    _: None = Depends(rate_limit("llm_config.write", limit=settings.rate_limit_default_per_min)),
) -> ApiResponse[LlmProvidersOut]:
    try:
        await run_in_threadpool(delete_provider, name)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    await audit(
        "llm_config.provider.delete",
        actor_id=actor.id,
        target_type="llm_provider",
        target_id=name,
    )
    result = await run_in_threadpool(list_providers)
    return ApiResponse.ok(_providers_out(result))


@router.post(
    "/providers/{name}/rename",
    summary="重命名供应商（同步迁移 env 键、模型前缀、降级链）",
)
async def rename_provider_endpoint(
    name: str,
    payload: RenameProviderIn,
    session: SessionDep,
    actor: WriteDep,
    _: None = Depends(rate_limit("llm_config.write", limit=settings.rate_limit_default_per_min)),
) -> ApiResponse[LlmProviderOut]:
    try:
        result = await run_in_threadpool(rename_provider, name, payload.name)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    # 运行期随机改写配置里可能存着 old/* 的模型 id；一并跟着改名。
    await sync_random_settings_after_rename(
        session, name, result, actor=actor.id
    )
    await audit(
        "llm_config.provider.rename",
        actor_id=actor.id,
        target_type="llm_provider",
        target_id=result.provider.name,
        detail={
            "from": name,
            "to": result.provider.name,
            "models": len(result.provider.models),
        },
    )
    return ApiResponse.ok(_provider_out(result.provider))


@router.put("/model-chain", summary="保存默认模型与降级顺序（第一项为默认）")
async def put_model_chain(
    payload: ModelChainIn,
    actor: WriteDep,
    _: None = Depends(rate_limit("llm_config.write", limit=settings.rate_limit_default_per_min)),
) -> ApiResponse[LlmProvidersOut]:
    """模型优先链：第一项是默认模型，后续项按顺序降级。

    每个 id 都必须已存在于某个供应商的模型映射里 —— 链指向不存在的模型
    等于把故障留到调用时；这里当场拒掉。
    """
    try:
        chain = await run_in_threadpool(save_model_chain, payload.models)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    await audit(
        "llm_config.model_chain.save",
        actor_id=actor.id,
        target_type="llm_model_chain",
        # 只看顺序与数量；链里没有密钥
        detail={"models": len(chain), "default": chain[0] if chain else ""},
    )
    result = await run_in_threadpool(list_providers)
    return ApiResponse.ok(_providers_out(result))


@router.post("/providers/{name}/models:fetch", summary="从供应商网关拉取真实模型 id")
async def fetch_models(
    name: str,
    actor: WriteDep,
    payload: FetchModelsIn | None = None,
    _: None = Depends(rate_limit("llm_config.write", limit=settings.rate_limit_default_per_min)),
) -> ApiResponse[FetchModelsOut]:
    """GET {base_url}/models，返回上游 id 列表。只读外网请求。

    新供应商还没保存，网关/密钥由请求体带过来；已保存的可以省略请求体，
    或只带改过的字段。这样「填完 → 拉模型 → 勾选」不必先存一遍。
    """
    result = await run_in_threadpool(list_providers)
    provider = next((p for p in result.providers if p.name == name), None)
    if provider is None:
        try:
            name = normalize_provider_name(name)
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc

    # 明文只在服务端内部流转：ProviderOut 刻意不回显密钥。
    saved_key = credentials_of(name)[1] if provider is not None else ""
    base_url, api_key = _fetch_target(
        provider,
        saved_key,
        payload.base_url if payload else "",
        payload.api_key if payload else "",
    )
    try:
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.get(
                base_url.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if response.status_code >= 400:
                raise BadRequest(f"供应商拒绝拉取模型列表（HTTP {response.status_code}）")
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise BadRequest(f"拉取模型列表失败：{exc}") from exc

    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise BadRequest("供应商返回的模型列表格式不对")
    ids = [
        UpstreamModelOut(id=str(item.get("id") or ""))
        for item in body["data"]
        if isinstance(item, dict) and item.get("id")
    ]
    await audit(
        "llm_config.models.fetch",
        actor_id=actor.id,
        target_type="llm_provider",
        target_id=name,
        # 只记数量与网关；模型名不是密钥，网关是管理员可见的配置
        detail={"models": len(ids), "base_url": base_url},
    )
    return ApiResponse.ok(FetchModelsOut(models=ids))


@router.post("/providers/{name}/test", summary="对供应商发一次真实最小请求")
async def test_provider(
    name: str,
    actor: WriteDep,
    _: None = Depends(rate_limit("llm_config.test", limit=settings.rate_limit_default_per_min)),
    model_id: str = Query(default="", description="平台模型 id，留空取该供应商第一个"),
) -> ApiResponse[dict]:
    """连通验收：验的是文本真的能取回来，不是 HTTP 200。"""
    result = await run_in_threadpool(list_providers)
    provider = next((p for p in result.providers if p.name == name), None)
    if provider is None:
        raise BadRequest(f"供应商 {name} 不存在")
    mapping = _select_model(provider, model_id)

    try:
        config = provider_config(name)
    except ValueError as exc:
        raise BadRequest(f"供应商 {name} 的配置有误：{exc}") from exc
    if not config.base_url.strip():
        raise BadRequest(
            f"LLM 供应商 {name} 未配置：请设置 {config.base_url_env}（OpenAI 兼容端点）"
        )
    if not config.api_key.strip():
        raise BadRequest(f"LLM 供应商 {name} 未配置：请设置 {config.api_key_env}")

    # 超时与正式调用同源（LLM_<NAME>_TIMEOUT_SEC）；不再固定 30s，免得推理
    # 模型被误判为连不通。
    settings_for_test = LLMSettings(
        base_url=config.base_url,
        api_key=config.api_key,
        timeout_sec=config.timeout_sec,
    )
    model_map = {m.id: m.upstream for m in provider.models}
    client = OpenAICompatibleClient(
        settings_for_test,
        model_map=model_map,
        api_key_env=config.api_key_env,
        vendor=f"LLM {name}",
    )
    try:
        answer = await client.complete(
            LlmRequest(
                model=mapping.id,
                messages=(LlmMessage("user", "只回复 OK 两个字，用于连通性检查"),),
            )
        )
    except AppError:
        # client 已按状态码分好类（401/403/429/5xx/缺 key）。这里再包一层
        # BadRequest 会把分类抹平，还可能把上游错误正文透给用户。原样抛。
        raise
    except httpx.HTTPError as exc:
        # 网络层异常（连不上 / 超时）client 没包装：给一句不泄露细节的提示。
        raise BadRequest("连通测试失败：无法连接到供应商网关") from exc

    await audit(
        "llm_config.provider.test",
        actor_id=actor.id,
        target_type="llm_provider",
        target_id=name,
        # 记连通性结果、网关与 token 用量；不记密钥与回复正文
        detail={
            "model": mapping.id,
            "base_url": config.base_url,
            "finish_reason": answer.finish_reason,
            "prompt_tokens": answer.usage.prompt_tokens,
            "completion_tokens": answer.usage.completion_tokens,
        },
    )
    return ApiResponse.ok(
        {
            "model": answer.model,
            "finishReason": answer.finish_reason,
            "text": answer.text[:80],
            "promptTokens": answer.usage.prompt_tokens,
            "completionTokens": answer.usage.completion_tokens,
        }
    )
