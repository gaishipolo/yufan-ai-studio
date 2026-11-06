"""替换任务展示名：列表、核对弹窗、档案详情、删除确认与任务中心共用同一规则。

规则收敛在这里而不是前端：任务中心读的是后端聚合结果，
如果两处各写一份，改一处就会让同一条 run 出现两个名字。
"""

from __future__ import annotations

import re

from modules.template.replacement_schema import ReplacementInputSummary

KIND_LABELS = {"character": "人物替换", "product": "产品替换", "scene": "场景替换"}
SOURCE_NAME_LIMIT = 18
MISSING_SOURCE = "历史档案未记录原片"
UNTITLED_SOURCE = "未命名原片"


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)


def source_name(summary: ReplacementInputSummary | None) -> str | None:
    if summary is None:
        return None
    return next((asset.name for asset in summary.assets if asset.role == "source_video"), None)


def source_title(name: str | None) -> str:
    """去掉扩展名并限长；超长以省略号收尾，避免撑破卡片。"""
    raw = MISSING_SOURCE if name is None else name.strip()
    cleaned = re.sub(r"\.mp4$", "", raw, flags=re.IGNORECASE) or UNTITLED_SOURCE
    characters = list(cleaned)
    return (
        f"{''.join(characters[:SOURCE_NAME_LIMIT])}…"
        if len(characters) > SOURCE_NAME_LIMIT
        else cleaned
    )


def task_name(source_name: str | None, kind: str, target: str, run_id: str) -> str:
    return f"{source_title(source_name)} · {kind_label(kind)} · {target} · #{run_id[:8].upper()}"
