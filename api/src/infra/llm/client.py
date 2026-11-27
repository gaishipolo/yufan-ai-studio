"""OpenAI 兼容协议的唯一 wire-format 实现（L2）。

职责边界：把 `LlmRequest` 发出去、把响应解析回 `LlmResponse`。
提示词模板永远由调用方拼 —— infra 不认识业务语义。

**自建 httpx client，不复用 `infra/http.get_http()`**：那个共享 client 是为
产物下载造的（GET stream + 逐跳 SSRF 校验 + 大小上限），全仓只有下载链路用它，
没有 POST 调用点。供应商 POST 一律自建，理由见设计文档 §7 —— 尤其是测试
要注入 `transport`，复用共享 client 就得在生产里留一个全局注入后门。
"""

from __future__ import annotations

from typing import Any

import httpx

from core.exceptions import AppError, BadRequest, Forbidden, RateLimited, Unauthorized
from infra.credentials import require_api_key
from infra.http_errors import classify_http_error
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
    resolve_model,
)

#: 状态映射是 LLM 自己声明的数据（不抹平各家的业务码）。
#: `classify_http_error` 默认 4xx→BadRequest / 5xx→UpstreamError，
#: 401/403/429 要显式给表才落到正确异常类。
_STATUS_MAP: dict[int, type[AppError]] = {
    401: Unauthorized,
    403: Forbidden,
    429: RateLimited,
}


class OpenAICompatibleClient:
    """一次调用一个实例；`transport` 仅供测试注入，生产恒为 None。"""

    def __init__(
        self,
        settings: LLMSettings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        model_map: dict[str, str] | None = None,
    ) -> None:
        self.settings = settings or get_llm_settings()
        self.transport = transport
        # 页面配的「平台 id → 上游模型名」映射；不进 LLMSettings（那是 env 字段）
        self.model_map = model_map

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
            vendor="LLM",
            env_name="LLM_API_KEY",
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
        first = choices[0] if isinstance(choices, list) and choices else {}
        message = first.get("message") if isinstance(first, dict) else {}
        if not isinstance(message, dict):
            message = {}
        return LlmResponse(
            text=str(message.get("content") or ""),
            model=str(body.get("model") or ""),
            finish_reason=str(first.get("finish_reason") or "") if isinstance(first, dict) else "",
            usage=self._parse_usage(body),
            tool_calls=self._parse_tool_calls(message),
            raw_id=str(body.get("id") or ""),
        )

    async def complete(self, request: LlmRequest) -> LlmResponse:
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
        # params 透传（temperature / max_tokens / tools / ...），与生图一致。
        payload.update(dict(request.params))

        async with self._client() as client:
            # 不重试：chat 是 POST，重复提交等于重复计费（infra/http.py 同一纪律）。
            response = await client.post(
                self._url(),
                json=payload,
                headers=self._headers(),
            )
            if response.status_code >= 400:
                raise classify_http_error(response, vendor="LLM", status_map=_STATUS_MAP)
            try:
                body = response.json()
            except ValueError as exc:
                raise BadRequest("LLM 上游返回的不是 JSON") from exc
            if not isinstance(body, dict):
                raise BadRequest("LLM 上游返回的 JSON 结构不对")
            return self._parse_response(body)


async def complete(request: LlmRequest) -> LlmResponse:
    """默认入口：用运维页配置的 OpenAI 兼容端点发一次调用。"""
    return await OpenAICompatibleClient().complete(request)


__all__ = ["OpenAICompatibleClient", "complete"]
