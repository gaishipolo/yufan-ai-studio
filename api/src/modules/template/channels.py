"""Per-run video channel selection; legacy configuration belongs to LibTV."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from core.exceptions import BadRequest

ReplacementChannel = Literal["libtv", "bailian", "bailian-prime"]

CHANNEL_MODELS = {
    "libtv": "libtv-wan3-replacement",
    "bailian": "bailian-wan3-replacement",
    "bailian-prime": "bailian-wan3-prime-replacement",
}
CHANNEL_LABELS = {
    "libtv": "LibTV · Wan 3.0 · 720P",
    "bailian": "阿里云百炼 · Wan 3.0 · 北京 · 720P",
    "bailian-prime": "阿里云百炼 · Wan 3.0 高速版 · 北京 · 720P",
}


def run_channel(configuration: Mapping | None) -> ReplacementChannel:
    value = (configuration or {}).get("channel", "libtv")
    if value not in CHANNEL_MODELS:
        raise BadRequest("任务的模型渠道无效，请核对原始配置")
    return cast(ReplacementChannel, value)


def channel_catalog() -> list[dict]:
    """渠道清单：模型在目录里、且供应商自报可用，才允许选。

    密钥一类的就绪判断由**供应商自己**回答（`InputPreparer.unavailable_reason`）：
    替换链不该知道某家供应商要配哪个环境变量，否则每接一个渠道就要改一次这里。
    """
    from infra import get_model_router
    from infra.ai.base import InputPreparer

    router = get_model_router()
    result = []
    for channel, model in CHANNEL_MODELS.items():
        enabled = router.resolve(model) is not None
        reason = "" if enabled else "服务端未启用此渠道"
        if enabled:
            provider = router.provider_for(model, allow_fallback=False)
            if isinstance(provider, InputPreparer):
                blocked = provider.unavailable_reason()
                if blocked:
                    enabled, reason = False, blocked
        result.append({"id": channel, "label": CHANNEL_LABELS[channel],
                       "model": model, "enabled": enabled,
                       "billing": "libtv" if channel == "libtv" else "aliyun",
                       "unavailable_reason": reason})
    return result


def require_channel(channel: ReplacementChannel) -> None:
    # LibTV remains compatible with installations that only register it at execution time.
    if channel == "libtv":
        return
    entry = next(item for item in channel_catalog() if item["id"] == channel)
    if not entry["enabled"]:
        raise BadRequest(f"{entry['label']}不可用：{entry['unavailable_reason']}")
