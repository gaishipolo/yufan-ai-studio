"""Per-run video channel selection; legacy configuration belongs to LibTV."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Literal, cast

from core.exceptions import BadRequest

logger = logging.getLogger(__name__)

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
    """读一行记录上的渠道。

    新请求的渠道由 `PrepareReplacement.channel`（Literal）与 `require_channel`
    校验，这里只服务**已经落库的行**。历史行里唯一出现过的渠道就是 libtv，
    所以「认不出来」不能抛错：这个函数处在启动恢复、轮询、详情投影三条链路上，
    一条手工改库或旧格式留下的脏行足以让整个进程起不来（`resume` 逐行兜底只会
    把它变成静默跳过，进程照旧）。降级 + 留痕才是可运维的形状。
    """
    if not isinstance(configuration, Mapping):
        if configuration is not None:
            logger.warning("任务配置不是对象，按 libtv 处理: %r", configuration)
        return "libtv"
    value = configuration.get("channel", "libtv")
    if value not in CHANNEL_MODELS:
        logger.warning("任务的模型渠道 %r 无法识别，按 libtv 处理（历史数据）", value)
        return "libtv"
    return cast(ReplacementChannel, value)


def channel_catalog() -> list[dict]:
    from infra import get_model_router

    router = get_model_router()
    result = []
    for channel, model in CHANNEL_MODELS.items():
        enabled = router.resolve(model) is not None
        reason = "" if enabled else "服务端未启用此渠道"
        if enabled and channel != "libtv":
            from infra.ai.bailian import get_bailian_settings

            if not get_bailian_settings().api_key.strip():
                enabled, reason = False, "服务端尚未配置百炼密钥"
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
