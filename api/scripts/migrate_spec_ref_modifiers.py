"""把带修饰符的槽位引用降级成单个引用（2026-11-27）。

## 为什么

一槽一素材（契约里 `max_count` 恒为 1），「整组引用」（`{{slot:x*}}`）与
「第 N 张引用」（`{{slot:x.2}}`）这两种写法**不存在**：

- 整组引用会**绕过「选中时文案」**（填了素材时直接插编号，作者写的那句措辞被无视）；
- 「第 N 张」在没填素材时**必然报错**（一个槽位只有一张，哪来的第 2 张）。

界面上给不出这两种候选（`selfRefChips` 只在 `maxCount > 1` 时才给），但
**粘贴**能塞进来 → 后端结构校验直接拒（`rules.py::_validate_ref_modifier`）。

## 这个脚本做什么

把存量数据里这两种写法**降级成单个引用**：

    {{Mixed 2*}}   →  {{Mixed 2}}
    {{Mixed 2.2}}  →  {{Mixed 2}}
    {{slot:x*}}    →  {{slot:x}}
    {{slot:x.2}}   →  {{slot:x}}

「第 N 张」降成单张是**唯一说得通的解**：一个槽位只有一张，那「第 2 张」从来
就不存在，降成单张（= 唯一那张）之后模板才跑得起来。

范围：已发布版本的正文与槽位文案、草稿、案例快照。

幂等：只把带修饰符的写法改掉，改完再跑没有可改的 → 全 0。

## 用法

    python scripts/migrate_spec_ref_modifiers.py --dry-run   # 只看影响面
    python scripts/migrate_spec_ref_modifiers.py             # 实跑
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from sqlalchemy import select  # noqa: E402

from db.session import get_session_factory  # noqa: E402
from modules.template_pack.models import (  # noqa: E402
    TemplateCase,
    TemplateSpec,
    TemplateSpecSlot,
    TemplateSpecVersion,
)

#: 带修饰符的引用：整组（*）与第 N 张（.N）。两种形态都要降级成单个引用。
# 修饰符前允许空格：`{{Mixed 2 *}}` 也会原样进提示词，迁移必须一起清掉。
MODIFIER_REF = re.compile(
    r"\{\{\s*(Mixed\s+\d+|slot:[a-z][a-z0-9_]{0,39})"
    r"\s*(?:\*|\.\d+)\s*\}\}"
)


def downgrade(text: str) -> tuple[str, int]:
    """把带修饰符的引用降级成单个引用，返回（新文本，改了几处）。"""
    if not text:
        return text, 0
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return "{{" + match.group(1) + "}}"

    return MODIFIER_REF.sub(repl, text), count


def _downgrade_spec_json(spec: dict[str, Any]) -> int:
    """一份契约 JSON（草稿 / 案例快照）里改了几处。"""
    changed = 0
    for field in ("prompt", "randomPrompt"):
        if spec.get(field):
            spec[field], hits = downgrade(str(spec[field]))
            changed += hits
    for slot in spec.get("slots") or []:
        for field in ("filledText", "emptyText"):
            if slot.get(field):
                slot[field], hits = downgrade(str(slot[field]))
                changed += hits
    return changed


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把带修饰符的槽位引用降级成单个引用")
    parser.add_argument("--dry-run", action="store_true", help="只看会改什么，不写库")
    args = parser.parse_args(argv)

    factory = get_session_factory()
    version_hits = 0
    slot_hits = 0
    spec_hits = 0
    case_hits = 0

    async with factory() as session:
        # ---- 已发布版本：正文（两段）+ 槽位文案（两段）
        versions = (await session.execute(select(TemplateSpecVersion))).scalars().all()
        rows = (await session.execute(select(TemplateSpecSlot))).scalars().all()
        by_version: dict[str, list[Any]] = {}
        for row in rows:
            by_version.setdefault(row.version_id, []).append(row)

        for version in versions:
            for field in ("prompt", "random_prompt"):
                text = getattr(version, field) or ""
                new_text, hits = downgrade(text)
                if hits:
                    version_hits += hits
                    print(f"版本 {version.id} 的 {field}：{hits} 处")
                    if not args.dry_run:
                        setattr(version, field, new_text)
            for row in by_version.get(version.id, []):
                for field in ("filled_text", "empty_text"):
                    text = getattr(row, field) or ""
                    new_text, hits = downgrade(text)
                    if hits:
                        slot_hits += hits
                        print(f"版本 {version.id} 槽位 {row.slot_key} 的 {field}：{hits} 处")
                        if not args.dry_run:
                            setattr(row, field, new_text)

        # ---- 草稿
        heads = (await session.execute(select(TemplateSpec))).scalars().all()
        for head in heads:
            draft = head.draft or {}
            if not isinstance(draft, dict) or not draft.get("slots"):
                continue
            # 深拷贝：JSON 列按「序列化后的值」判脏，原地改嵌套会被当成没变。
            draft = json.loads(json.dumps(draft))
            hits = _downgrade_spec_json(draft)
            if hits:
                spec_hits += hits
                print(f"草稿 {head.spec_key}：{hits} 处")
                if not args.dry_run:
                    head.draft = draft

        # ---- 案例快照
        cases = (await session.execute(select(TemplateCase))).scalars().all()
        for case in cases:
            config = getattr(case, "config", None)
            if not isinstance(config, dict) or not config.get("slots"):
                continue
            config = json.loads(json.dumps(config))
            hits = _downgrade_spec_json(config)
            if hits:
                case_hits += hits
                print(f"案例 {case.id}：{hits} 处")
                if not args.dry_run:
                    case.config = config

        if not args.dry_run:
            await session.commit()

    prefix = "（干跑）" if args.dry_run else ""
    print()
    print(f"{prefix}已发布版本的正文：{version_hits} 处")
    print(f"{prefix}已发布版本的槽位文案：{slot_hits} 处")
    print(f"{prefix}草稿：{spec_hits} 处")
    print(f"{prefix}案例快照：{case_hits} 处")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
