"""OpenAI 兼容协议的唯一 wire-format 实现（L2）。

职责边界：把 `LlmRequest` 发出去、把响应解析回 `LlmResponse`。
提示词模板永远由调用方拼 —— infra 不认识业务语义。

**自建 httpx client，不复用 `infra/http.get_http()`**：那个共享 client 是为
产物下载造的（GET stream + 逐跳 SSRF 校验 + 大小上限），全仓只有下载链路用它，
没有 POST 调用点。供应商 POST 一律自建，理由见设计文档 §7 —— 尤其是测试
要注入 `transport`，复用共享 client 就得在生产里留一个全局注入后门。
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import httpx

from core.exceptions import (
    AppError,
    BadRequest,
    Forbidden,
    RateLimited,
    Unauthorized,
    UpstreamError,
)
from core.logging import get_logger
from infra.credentials import require_api_key
from infra.http_errors import classify_http_error, extract_detail
from infra.llm.base import (
    LlmMessage,
    LlmRequest,
    LlmResponse,
    LlmToolCall,
    LlmUsage,
)
from infra.llm.providers.openai_compatible import (
    LLMSettings,
    get_llm_settings,
    load_provider,
    resolve_model,
)
from infra.llm.providers.source import provider_of

logger = get_logger(__name__)

#: 默认模型 id：表示「用页面配置的优先链」。
DEFAULT_MODEL = "llm/default"

#: 结构由 LlmRequest 字段决定的参数，不允许从 params 再传一遍把它覆盖掉。
_RESERVED_PARAMS = frozenset({"model", "messages", "stream"})

#: 状态映射是 LLM 自己声明的数据（不抹平各家的业务码）。
#: `classify_http_error` 默认 4xx→BadRequest / 5xx→UpstreamError，
#: 401/403/429 要显式给表才落到正确异常类。
_STATUS_MAP: dict[int, type[AppError]] = {
    401: Unauthorized,
    403: Forbidden,
    429: RateLimited,
}


def _checked_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """params 只透传业务参数；结构字段不能被它覆盖。"""
    reserved = sorted(_RESERVED_PARAMS.intersection(params))
    if reserved:
        raise BadRequest(
            "LLM params 不能包含保留键："
            + "、".join(reserved)
            + "（model/messages 由请求字段决定；V1 不支持 stream）"
        )
    return dict(params)


class OpenAICompatibleClient:
    """一次调用一个实例；`transport` 仅供测试注入，生产恒为 None。"""

    def __init__(
        self,
        settings: LLMSettings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        model_map: dict[str, str] | None = None,
        api_key_env: str = "LLM_API_KEY",
        vendor: str = "LLM",
    ) -> None:
        self.settings = settings or get_llm_settings()
        self.transport = transport
        # 页面配的「平台 id → 上游模型名」映射；不进 LLMSettings（那是 env 字段）
        self.model_map = model_map
        # 具名供应商的密钥键不同；错误文案必须指向用户真正要配的那个键。
        self.api_key_env = api_key_env
        self.vendor = vendor

    def _client(self) -> httpx.AsyncClient:
        # trust_env=False 与驱动一致：不吃系统代理，避免容器里的 HTTP_PROXY
        # 把供应商请求劫到别处，还让「换网关」变成改环境变量的隐形依赖。
        return httpx.AsyncClient(
            transport=self.transport,
            timeout=self.settings.request_timeout(),
            trust_env=False,
        )

    def _headers(self) -> dict[str, str]:
        key = require_api_key(
            self.settings.bearer_token(),
            vendor=self.vendor,
            env_name=self.api_key_env,
            note="OpenAI 兼容端点",
        )
        return {"Authorization": f"Bearer {key}"}

    def _url(self) -> str:
        if not self.settings.base_url.strip():
            # 与 require_api_key 同一口径：配置缺失是 4xx，不是 5xx。
            raise BadRequest("LLM 未配置：请设置 LLM_BASE_URL（OpenAI 兼容端点）")
        return self.settings.chat_completions_url()

    def _wire_message(self, message: LlmMessage) -> dict[str, Any]:
        """LlmMessage → OpenAI message。有值才发，不塞空串给上游。"""
        wire: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.name:
            wire["name"] = message.name
        if message.tool_call_id:
            wire["tool_call_id"] = message.tool_call_id
        return wire

    def _redact(self, text: str) -> str:
        """上游错误正文可能回显 key；出站前必须抹掉。"""
        key = self.settings.bearer_token()
        return text.replace(key, "***") if key else text

    def _bad_response(self, detail: str) -> UpstreamError:
        error = UpstreamError(f"{self.vendor} 上游响应异常：{detail}")
        # 200 但结构不对：保留原始状态供降级链判断（不是 408，可降级）。
        error.upstream_status = 200
        return error

    @staticmethod
    def _body_error_detail(body: dict[str, Any]) -> str:
        error = body.get("error")
        if isinstance(error, str) and error.strip():
            return error.strip()
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        try:
            text = json.dumps(error, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(error)
        return text.strip()[:300] or "（上游没有给错误正文）"

    def _content_text(self, content: Any) -> str:
        """把 content 收敛为文本。

        OpenAI 兼容端点的 content 可能是字符串，也可能是多模态片段数组；
        后者只取 text 片段。其它结构不能 str() 成 Python repr 透出去。
        """
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "".join(parts)
        raise self._bad_response("message.content 既不是字符串也不是片段数组")

    @staticmethod
    def _parse_usage(body: dict[str, Any]) -> LlmUsage:
        raw = body.get("usage")
        if not isinstance(raw, dict):
            # 某些兼容端点不回 usage：给全 0，不猜。将来 agent 记账看到 0
            # 就知道上游没给，比编一个数字诚实。
            return LlmUsage(0, 0, 0)
        return LlmUsage(
            prompt_tokens=int(raw.get("prompt_tokens") or 0),
            completion_tokens=int(raw.get("completion_tokens") or 0),
            total_tokens=int(raw.get("total_tokens") or 0),
        )

    @staticmethod
    def _parse_tool_calls(message: dict[str, Any]) -> tuple[LlmToolCall, ...]:
        raw = message.get("tool_calls")
        if not isinstance(raw, list):
            return ()
        calls: list[LlmToolCall] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            fn = item.get("function")
            if not isinstance(fn, dict):
                continue
            # arguments 保持 OpenAI 原样的 JSON 字符串，不在这里 loads。
            calls.append(
                LlmToolCall(
                    id=str(item.get("id") or ""),
                    name=str(fn.get("name") or ""),
                    arguments=str(fn.get("arguments") or ""),
                )
            )
        return tuple(calls)

    def _parse_response(self, body: dict[str, Any]) -> LlmResponse:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            # 200 + {"error": ...} 是网关常见的"假成功"；当成功空文本会把
            # 上游故障伪装成"模型什么都没说"。
            if body.get("error") is not None:
                detail = self._redact(self._body_error_detail(body))
                raise self._bad_response(f"HTTP 200 里返回了错误：{detail}")
            raise self._bad_response("缺少 choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise self._bad_response("choices[0] 不是对象")
        message = first.get("message")
        if not isinstance(message, dict):
            raise self._bad_response("choices[0].message 不是对象")
        return LlmResponse(
            text=self._content_text(message.get("content")),
            model=str(body.get("model") or ""),
            finish_reason=str(first.get("finish_reason") or ""),
            usage=self._parse_usage(body),
            tool_calls=self._parse_tool_calls(message),
            raw_id=str(body.get("id") or ""),
        )

    async def complete(
        self,
        request: LlmRequest,
        *,
        timeout_sec: float | None = None,
    ) -> LlmResponse:
        """发一次调用。`timeout_sec` 覆盖客户端默认超时，仅作用这一次请求。"""
        if timeout_sec is not None and timeout_sec <= 0:
            raise BadRequest("LLM 单次超时必须大于 0 秒")
        # 平台 id → 上游 model 名：未知 id 抛 BadRequest（G2），不静默回落。
        try:
            upstream_model = resolve_model(request.model, self.settings, self.model_map)
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc

        payload: dict[str, Any] = {
            "model": upstream_model,
            # G1：messages 原样按序发出去，role/content/name/tool_call_id 都不丢。
            "messages": [self._wire_message(m) for m in request.messages],
        }
        # params 透传（temperature / max_tokens / tools / ...），与生图一致；
# 但 model/messages/stream 是结构字段，不允许从 params 覆盖。
        payload.update(_checked_params(request.params))

        async with self._client() as client:
            # 不重试：chat 是 POST，重复提交等于重复计费（infra/http.py 同一纪律）。
            # `timeout` 是 per-request 覆盖；传 None 会变成「永不超时」，
            # 所以只有显式给了才放进请求参数。
            post_kwargs: dict[str, Any] = {
                "json": payload,
                "headers": self._headers(),
            }
            if timeout_sec is not None:
                post_kwargs["timeout"] = timeout_sec
            response = await client.post(self._url(), **post_kwargs)
            if response.status_code >= 400:
                # 上游错误正文可能回显 key；先脱敏再构造异常，args/message 都不带明文。
                detail = self._redact(extract_detail(response))
                raise classify_http_error(
                    response,
                    vendor=self.vendor,
                    status_map=_STATUS_MAP,
                    detail=detail,
                )
            try:
                body = response.json()
            except ValueError as exc:
                raise BadRequest("LLM 上游返回的不是 JSON") from exc
            if not isinstance(body, dict):
                raise BadRequest("LLM 上游返回的 JSON 结构不对")
            parsed = self._parse_response(body)
            return replace(parsed, platform_model=request.model)


def _should_fallback(exc: BaseException) -> bool:
    """这个失败是否允许换下一个模型。

    只降级「明确失败」：

    - 429 / 401 / 403 / 5xx：上游明确拒绝或不可用；
    - 连接失败（``ConnectError`` / ``ConnectTimeout``）：请求根本没送出去。

    不降级：400/422（参数/请求错，换模型只会得到同样的错）、408 与读写超时
    （请求已发出，上游**可能已受理并计费**，再打一次是重复计费）。
    """
    if isinstance(exc, (Unauthorized, Forbidden, RateLimited)):
        return True
    if isinstance(exc, UpstreamError):
        return getattr(exc, "upstream_status", None) != 408
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))


def _now() -> float:
    """可被测试替换的单调时钟。"""
    return time.monotonic()


def _timeout_for(timeout_sec: float | None, deadline: float | None) -> float | None:
    """这次尝试实际能用的超时。

    - 没给 deadline：原样返回 `timeout_sec`（`None` = 用客户端默认）。
    - 给了 deadline：返回 `min(timeout_sec, 剩余预算)`；剩余 ≤ 0 返回 0，
      调用方据此**不再发请求**，而不是发一个 0 秒超时的请求。

    必须把 deadline 压进单次 timeout，否则一个 20s 的请求可以越过 45s 总预算
    才失败 —— 总预算就只是「下一轮不再发起」，挡不住正在跑的那一轮。
    """
    if deadline is None:
        return timeout_sec
    remaining = deadline - _now()
    if remaining <= 0:
        return 0.0
    if timeout_sec is None:
        return remaining
    return min(timeout_sec, remaining)


async def _complete_one(
    request: LlmRequest,
    platform_id: str,
    *,
    timeout_sec: float | None = None,
) -> LlmResponse:
    runtime = load_provider(provider_of(platform_id))
    client = OpenAICompatibleClient(
        runtime.settings,
        model_map=runtime.model_map,
        api_key_env=runtime.api_key_env,
        vendor=runtime.label,
    )
    return await client.complete(
        replace(request, model=platform_id),
        timeout_sec=timeout_sec,
    )


async def complete(
    request: LlmRequest,
    *,
    timeout_sec: float | None = None,
    deadline: float | None = None,
) -> LlmResponse:
    """默认入口：用运维页配置的 OpenAI 兼容端点发一次调用。

    只有 ``llm/default`` 会走 ``LLM_MODEL_CHAIN``（第一项为默认模型）并按
    失败类型降级；显式模型 id 精确调用，不静默换模型。

    `timeout_sec` / `deadline` 是给「一次业务操作只能等 N 秒」的调用方用的：
    - `timeout_sec` 覆盖该次请求的客户端默认超时；
    - `deadline` 是**单调时钟的绝对时间点**（`time.monotonic()`），降级链在
      每次尝试前检查，且单次 timeout 不超过剩余预算。不给就沿用旧行为。
    """
    if timeout_sec is not None and timeout_sec <= 0:
        raise BadRequest("LLM 单次超时必须大于 0 秒")

    if request.model != DEFAULT_MODEL:
        attempt_timeout = _timeout_for(timeout_sec, deadline)
        if attempt_timeout is not None and attempt_timeout <= 0:
            raise UpstreamError("LLM 调用超过总预算：未发出请求")
        return await _complete_one(request, request.model, timeout_sec=attempt_timeout)

    try:
        chain = get_llm_settings().model_chain_ids()
    except ValueError as exc:
        raise BadRequest(f"LLM 模型优先链配置有误：{exc}") from exc
    if not chain:
        # 未配置链时保持旧语义：llm/default → LLM_MODEL / 模型表第一项。
        chain = (DEFAULT_MODEL,)

    last_exc: BaseException | None = None
    for index, platform_id in enumerate(chain):
        attempt_timeout = _timeout_for(timeout_sec, deadline)
        if attempt_timeout is not None and attempt_timeout <= 0:
            if last_exc is not None:
                raise last_exc
            raise UpstreamError("LLM 调用超过总预算：未发出请求")
        try:
            return await _complete_one(
                request,
                platform_id,
                timeout_sec=attempt_timeout,
            )
        except Exception as exc:
            last_exc = exc
            is_last = index == len(chain) - 1
            if is_last or not _should_fallback(exc):
                raise
            logger.warning(
                "LLM 默认模型 %s 调用失败（%s），降级到下一个模型",
                platform_id,
                type(exc).__name__,
            )
    raise AssertionError("LLM 降级链为空")  # pragma: no cover - 循环至少一项


__all__ = ["DEFAULT_MODEL", "OpenAICompatibleClient", "complete"]
