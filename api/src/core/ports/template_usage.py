"""模板的累计使用次数：生成模块提供只读计数，模板模块聚合。

模板卡片只能看到 `template_specs`，而「用过几次」分散在 `video_tasks` /
`image_jobs`。业务模块之间不能互相 import，所以这里开一个**多实现**注入口：
视频 / 生图各自按 `template_key` 计数，模板模块只认这份契约。

与 `usage_metrics` 分开：那个回答「某段时间产出了多少」，这个回答「这张模板
从上线到现在被用过几次」——口径不同，混在一起会让卡片数字被时间筛选影响。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from core.ports.host import Port


@runtime_checkable
class TemplateUsageProvider(Protocol):
    """一种生成能力的模板使用次数读取器。"""

    async def template_counts(self, template_keys: Sequence[str]) -> Mapping[str, int]:
        """返回这批模板 key 各自的**尝试次数**（含失败/进行中）。"""


_providers: Port[TemplateUsageProvider] = Port("template-usage", multi=True)


def register_template_usage(key: str, provider: TemplateUsageProvider) -> None:
    """注册一份模板使用次数读取器。"""
    _providers.provide(provider, key=key, owner="core.ports.template_usage")


def reset_template_usage() -> None:
    """仅测试用：清空注册。"""
    _providers.reset()


def has_template_usage() -> bool:
    """装配自检用：有没有接上任何模板计数来源。"""
    return _providers.has()


async def template_usage_counts(template_keys: Sequence[str]) -> dict[str, int]:
    """合并所有来源的计数；没有注册来源时返回空字典。"""
    wanted = tuple(
        dict.fromkeys(key.strip() for key in template_keys if key and key.strip())
    )
    if not wanted:
        return {}
    totals: dict[str, int] = {}
    for _key, provider in sorted(_providers.items().items()):
        counts = await provider.template_counts(wanted)
        for template_key, count in counts.items():
            if template_key:
                totals[template_key] = totals.get(template_key, 0) + int(count)
    return totals


__all__ = [
    "TemplateUsageProvider",
    "has_template_usage",
    "register_template_usage",
    "reset_template_usage",
    "template_usage_counts",
]