"""将模板调用摘要提供给任务聚合注入点。"""

from core.feed import ReplacementFeedRecord
from modules.template.replacement_service import feed_records


def read_feed(owner: str) -> list[ReplacementFeedRecord]:
    return feed_records(owner)
