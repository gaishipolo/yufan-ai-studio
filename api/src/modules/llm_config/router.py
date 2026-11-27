"""LLM 配置页 REST：供应商 CRUD + 拉取上游模型 + 连通测试。

只管**配置**（写 .env），不碰 `infra.llm` 的调用协议 —— 那是能力层的事。
拉取模型和连通测试会发真实外网请求，是部署验收动作，不在 doctor 里跑。
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Query
from starlette.concurrency import run_in_threadpool

from api.deps import rate_limit, require
from core.audit import record as audit
from core.config import settings
from core.context import CurrentUser
from core.exceptions import AppError, BadRequest
from core.response import ApiResponse
from infra.llm import LlmMessage, LlmRequest, OpenAICompatibleClient
from infra.llm.providers.openai_compatible import LLMSettings
from modules.llm_config.schema import (
    FetchModelsOut,
    LlmModelOut,
    LlmProviderOut,
    LlmProvidersOut,
    SaveProviderIn,
    UpstreamModelOut,
)
from modules.llm_config.store import (
    ModelMapping,
    credentials_of,
    delete_provider,
    list_providers,
    save_provider,
)

router = APIRouter(prefix="/llm-config", tags=["LLM 配置"])

ReadDep = Annotated[CurrentUser, Depends(require("llm_config:read"))]
WriteDep = Annotated[CurrentUser, Depends(require("llm_config:write"))]


def _provider_out(item) -> LlmProviderOut:
    return LlmProviderOut(
        name=item.name,
        base_url=item.base_url,
        api_key_configured=item.api_key_configured,
        api_key_masked=item.api_key_masked,
        api_key_fingerprint=item.api_key_fingerprint,
        models=[LlmModelOut(id=m.id, upstream=m.upstream) for m in item.models],
        source=item.source,
    )


@router.get("/providers", summary="列出 LLM 供应商配置（密钥脱敏）")
async def get_providers(actor: ReadDep) -> ApiResponse[LlmProvidersOut]:
    result = await run_in_threadpool(list_providers)
    return ApiResponse.ok(
        LlmProvidersOut(providers=[_provider_out(item) for item in result.providers])
    )


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
        # 审计只记「改了哪家、几个模型」，绝不记密钥/网关明文
        detail={"models": len(result.models), "api_key_changed": bool(payload.api_key.strip())},
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
    return ApiResponse.ok(
        LlmProvidersOut(providers=[_provider_out(item) for item in result.providers])
    )


@router.post("/providers/{name}/models:fetch", summary="从供应商网关拉取真实模型 id")
async def fetch_models(
    name: str,
    actor: WriteDep,
    _: None = Depends(rate_limit("llm_config.write", limit=settings.rate_limit_default_per_min)),
) -> ApiResponse[FetchModelsOut]:
    """GET {base_url}/models，返回上游 id 列表。只读外网请求。"""
    result = await run_in_threadpool(list_providers)
    provider = next((p for p in result.providers if p.name == name), None)
    if provider is None:
        raise BadRequest(f"供应商 {name} 不存在")
    if not provider.base_url or not provider.api_key_configured:
        raise BadRequest("请先填好网关地址和密钥，再拉取模型")

    # 明文只在服务端内部流转：ProviderOut 刻意不回显密钥。
    # 不能用 `_, api_key = ...`：本函数的 `_` 是 rate_limit 依赖参数，
    # 类型是 None，把 str 赋给它会被 mypy 判 Incompatible types in assignment。
    api_key = credentials_of(name)[1]
    try:
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.get(
                provider.base_url.rstrip("/") + "/models",
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
    if not provider.models:
        raise BadRequest("该供应商还没有配置模型映射")
    mapping = next((m for m in provider.models if m.id == model_id), provider.models[0])

    base_url, api_key = credentials_of(name)
    settings_for_test = LLMSettings(base_url=base_url, api_key=api_key, timeout_sec=30)
    model_map = {m.id: m.upstream for m in provider.models}
    try:
        answer = await OpenAICompatibleClient(settings_for_test, model_map=model_map).complete(
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
    return ApiResponse.ok(
        {
            "model": answer.model,
            "finishReason": answer.finish_reason,
            "text": answer.text[:80],
            "promptTokens": answer.usage.prompt_tokens,
            "completionTokens": answer.usage.completion_tokens,
        }
    )
