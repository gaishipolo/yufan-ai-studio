"""Per-run video channel selection; the model layer owns the actual routing.

模板库只认 channel 这个 UI 枚举与「读历史行」的降级逻辑。渠道对应哪个模型、
供应商是否可用、提交形态、提交前要不要上传素材，全部经
`core.ports.model_generation` 交给 `modules/model_driver` 回答
（见 `modules/model_driver/replacement_channels.py`）。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Literal, cast, get_args

from core.exceptions import BadRequest
from core.ports.model_generation import get_model_generation

logger = logging.getLogger(__name__)

ReplacementChannel = Literal["libtv", "bailian", "bailian-prime"]
_CHANNEL_IDS = frozenset(get_args(ReplacementChannel))


def run_channel(configuration: Mapping | None) -> ReplacementChannel:
    """读一行记录上的渠道。

    新请求的渠道由 `PrepareReplacement.channel`（Literal）与 `require_channel`
    校验；这里只服务**已经落库的行**。历史行里唯一出现过的渠道就是 libtv，
    所以「认不出来」不能抛错：这个函数处在启动恢复、轮询、详情投影三条链路上，
    一条手工改库或旧格式留下的脏行足以让整个进程起不来。降级 + 留痕才是可运维的形状。
    """
    if not isinstance(configuration, Mapping):
        if configuration is not None:
            logger.warning("任务配置不是对象，按 libtv 处理: %r", configuration)
        return "libtv"
    value = configuration.get("channel", "libtv")
    if value not in _CHANNEL_IDS:
        logger.warning("任务的模型渠道 %r 无法识别，按 libtv 处理（历史数据）", value)
        return "libtv"
    return cast(ReplacementChannel, value)


def model_for_channel(channel: ReplacementChannel) -> str:
    """冻结渠道 → 模型 id。启动恢复 / 轮询链靠它把历史行还原成 GenerationRecord。"""
    return get_model_generation().model_for_channel(channel)


def channel_label(channel: ReplacementChannel) -> str:
    return get_model_generation().channel_label(channel)


def submit_mode(model: str) -> str | None:
    """提交形态快照是增强字段：解析不出来留 None，绝不拖垮一次成功提交。"""
    try:
        return get_model_generation().submit_mode(model)
    except Exception:  # noqa: BLE001 - 快照是增强，不能让一次成功提交失败
        return None


async def prepare_inputs(model: str, run_id: str) -> None:
    """渠道要求「先上传素材」时，由模型驱动层调驱动的可选协议。"""
    await get_model_generation().prepare_inputs(model, run_id)


def channel_catalog() -> list[dict]:
    """渠道清单整体来自模型驱动层：模板库不认识模型 id，也不问供应商是否可用。"""
    return [
        {
            "id": item.id,
            "label": item.label,
            "model": item.model,
            "enabled": item.enabled,
            "billing": item.billing,
            "unavailable_reason": item.unavailable_reason,
        }
        for item in get_model_generation().replacement_channels()
    ]


def require_channel(channel: ReplacementChannel) -> None:
    # LibTV remains compatible with installations that only register it at execution time.
    if channel == "libtv":
        return
    entry = next(item for item in channel_catalog() if item["id"] == channel)
    if not entry["enabled"]:
        raise BadRequest(f"{entry['label']}不可用：{entry['unavailable_reason']}")
