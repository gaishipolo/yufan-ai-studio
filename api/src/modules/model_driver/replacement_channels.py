"""替换链的模型渠道：别名表、可用性、提交形态与提交前素材准备。

「渠道」是老模板库的 UI 别名（libtv / bailian / …），真正决定走哪家供应商的是
`model`。这张表和所有 `ModelRouter` / `InputPreparer` 调用都归模型驱动层：
`modules/template` 只保留 channel 这个枚举与读历史行的降级逻辑，不许自己
解析供应商实例。

为什么这些 id 不在 `models()` 里：三个替换模型都标了 `internal=True`（见
`infra.ai.base.ModelSpec`），故意不进公开模型目录——用户不该在生图/生视频
选择器里看到它们，但 `resolve()` / `provider_for()` 必须仍能按 id 解析，
否则已经落库的替换任务在重启后会被判死。
"""

from __future__ import annotations

from core.exceptions import Unavailable
from core.ports.model_generation import ReplacementChannelInfo
from infra.ai.base import InputPreparer

DEFAULT_CHANNEL = "libtv"

CHANNEL_MODELS: dict[str, str] = {
    "libtv": "libtv-wan3-replacement",
    "bailian": "bailian-wan3-replacement",
    "bailian-prime": "bailian-wan3-prime-replacement",
}
CHANNEL_LABELS: dict[str, str] = {
    "libtv": "LibTV · Wan 3.0 · 720P",
    "bailian": "阿里云百炼 · Wan 3.0 · 北京 · 720P",
    "bailian-prime": "阿里云百炼 · Wan 3.0 高速版 · 北京 · 720P",
}
CHANNEL_BILLING: dict[str, str] = {
    "libtv": "libtv",
    "bailian": "aliyun",
    "bailian-prime": "aliyun",
}


def channel_ids() -> frozenset[str]:
    """合法渠道集合。`run_channel()` 用它判历史脏行，不做任何模型解析。"""
    return frozenset(CHANNEL_MODELS)


def model_for_channel(channel: str) -> str:
    """冻结渠道 → 模型 id。脏行降级到默认渠道，与 `run_channel()` 同一口径。"""
    return CHANNEL_MODELS.get(channel, CHANNEL_MODELS[DEFAULT_CHANNEL])


def channel_label(channel: str) -> str:
    return CHANNEL_LABELS.get(channel, CHANNEL_LABELS[DEFAULT_CHANNEL])


def submit_mode(model: str) -> str | None:
    """提交形态快照。与 `image.pipeline` / `model_driver.store` 同一口径。

    整段吞异常：这是给启动恢复器分类用的增强字段，一次成功提交绝不能因为
    解析不出供应商形态而失败。解析不出来就留 `None`，让恢复器按「不能续查、
    请人核对」处理，不猜一个可能把记录判死的答案。
    """
    from infra import get_model_router

    try:
        router = get_model_router()
        if router.resolve(model) is None:
            return None
        provider = router.provider_for(model)
    except Exception:  # noqa: BLE001 - 快照是增强，不能让一次成功提交失败
        return None
    return "async" if getattr(provider, "supports_poll", False) else "sync"


def replacement_channels() -> tuple[ReplacementChannelInfo, ...]:
    """渠道清单：模型在目录里、且供应商自报可用，才允许选。

    密钥一类的就绪判断由**供应商自己**回答（`InputPreparer.unavailable_reason`）：
    替换链不该知道某家供应商要配哪个环境变量，否则每接一个渠道就要改一次。
    """
    from infra import get_model_router

    router = get_model_router()
    result: list[ReplacementChannelInfo] = []
    for channel, model in CHANNEL_MODELS.items():
        enabled = router.resolve(model) is not None
        reason = "" if enabled else "服务端未启用此渠道"
        if enabled:
            provider = router.provider_for(model, allow_fallback=False)
            if isinstance(provider, InputPreparer):
                blocked = provider.unavailable_reason()
                if blocked:
                    enabled, reason = False, blocked
        result.append(
            ReplacementChannelInfo(
                id=channel,
                label=CHANNEL_LABELS[channel],
                model=model,
                enabled=enabled,
                billing=CHANNEL_BILLING[channel],
                unavailable_reason=reason,
            )
        )
    return tuple(result)


async def prepare_inputs(model: str, run_id: str) -> None:
    """渠道要求「先上传素材」时走驱动的可选协议。

    替换链不认识具体供应商：能不能上传、密钥配没配，都由驱动自己回答。
    解析必须带 `allow_fallback=False` —— 只有一个供应商时的兜底会把任务悄悄
    交给另一家，已付费的替换任务最忌讳这个。
    """
    from infra import get_model_router

    provider = get_model_router().provider_for(model, allow_fallback=False)
    if not isinstance(provider, InputPreparer):
        raise Unavailable(f"渠道 {model} 不支持素材上传，无法提交")
    await provider.prepare_inputs(run_id, model=model)
