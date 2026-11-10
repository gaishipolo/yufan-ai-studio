"""提示词引用只根据当次冻结输入解析，不读取会变化的模板或素材包。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.exceptions import NotFound
from infra.media_probe import create_preview
from modules.template.schema import ReplacementInputSummary


def enrich_input_summary(
    summary: ReplacementInputSummary,
    spec: dict[str, Any] | None,
) -> ReplacementInputSummary:
    references = (spec or {}).get("references", [])
    bindings = {
        item["id"]: (index, item)
        for index, item in enumerate(references, 1)
        if isinstance(item, dict) and item.get("id")
    }
    assets = []
    counts = {"image": 0, "video": 0, "audio": 0}
    channel_tokens = {}
    if (spec or {}).get("channel") in {"bailian", "bailian-prime"}:
        for entry in references:
            kind = entry.get("type")
            if kind in counts:
                counts[kind] += 1
                label = {"image": "图", "video": "视频", "audio": "音频"}[kind]
                channel_tokens[entry["id"]] = f"{label}{counts[kind]}"
    for asset in summary.assets:
        data: dict[str, Any] = {}
        if asset.id in bindings:
            index, binding = bindings[asset.id]
            data.update(token=channel_tokens.get(asset.id, f"{{{{Mixed {index}}}}}"),
                        usage=binding.get("usage", ""))
        if asset.type in {"image", "video"}:
            data["preview_url"] = asset.url + "/thumbnail"
        assets.append(asset.model_copy(update=data))
    return summary.model_copy(update={"assets": assets})


def input_thumbnail(source: Path, kind: str) -> Path:
    """快照派生缩略图，原子替换；不能读取当前素材库的封面代替历史输入。"""
    target = source.parent.parent / "records" / "input-previews" / (source.stem + ".jpg")
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{source.stem}-{uuid4().hex}.jpg")
    try:
        if not create_preview(source, temporary, kind):
            raise NotFound("当次输入缩略图暂不可用，请查看原件")
        temporary.replace(target)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise NotFound("当次输入缩略图暂不可用，请查看原件") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return target
