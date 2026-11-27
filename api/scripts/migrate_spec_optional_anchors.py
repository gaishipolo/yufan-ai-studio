"""把老模板的「未选时文案」认成「来自正文里的框选段」（2026-11-27）。

## 为什么要有这一步

可选槽位的「未选时文案」按设计是**正文里框选出来的那一段**：提交时那段正文
让位成插入点，文字挪进 empty_text。老模板不是这样 —— 那是作者在老界面的
「未选时文案」框里**手填**的，正文里另有一处引用。

库里两种形态**长得一模一样**（一处引用 + 一句 empty_text），分不出来。
`empty_text_from_anchor` 那一列就是「是不是从框选段来的」；列是后加的，老
数据一律 False → 预检会要求作者在正文里重新框选一次。

## 但其实不用让作者动手

老模板正文里那处引用**就是它的插入点**（填了素材插编号，没填插那句兜底
文案）。把那句兜底文案「替换」到正文里那处引用的位置、再框选它 —— 得到的
存储态与原来**逐字相同**（一处引用 + 同一句 empty_text），只多了「来自框选段」
这一个标记。

所以迁移要做的只有一件事：**把符合条件的行刷成 True**。

## 刷哪些行

- 该槽位是可选的（参与编号、用户自己挑、min_count == 0）；
- `empty_text` 非空（否则没有「那句兜底文案」可言）；
- 该规格的提示词里，指向它的**单个引用恰好一处**。

最后一条是关键：

- **多于一处** → 分不出哪一处才是插入点（正文里可能有作者自己写的引用）→
  不刷，交给预检拦（作者自己删到只剩一处）；
- **零处** → 那句 empty_text 是死数据（没有插入点，用不上）→ 不刷；
- 只有**整组 / 第 N 张**引用 → 那些不是插入点 → 不刷。

## 幂等

只把 False 刷成 True，不碰别的数据，可以重复跑。

## 用法

    python scripts/migrate_spec_optional_anchors.py --dry-run   # 只看影响面
    python scripts/migrate_spec_optional_anchors.py             # 实跑
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

#: **单个**引用（没有修饰符）。整组（*）与第 N 张（.N）不是插入点，不算。
SINGLE_MIXED = re.compile(r"\{\{\s*Mixed\s+(\d+)\s*\}\}")
SINGLE_SLOT = re.compile(r"\{\{\s*slot:([a-z][a-z0-9_]{0,39})\s*\}\}")

BT = chr(96)


def visual_keys(slots: list[dict[str, Any]]) -> list[str]:
    """参与 {{Mixed N}} 编号的槽位 key，按编号顺序。

    与 core/template_spec/rules.py::visual_slot_keys **同一判据**
    （refMode == mixed、按 position 排序）—— 编号算错就会刷错标记。
    """
    ordered = sorted(slots, key=lambda s: (s.get("position", 0), s.get("key", "")))
    return [s["key"] for s in ordered if (s.get("refMode") or "mixed") == "mixed"]


def single_ref_counts(text: str, keys: list[str]) -> dict[str, int]:
    """一段提示词里，各槽位被**单个引用**了几次。"""
    counts: dict[str, int] = {}
    for match in SINGLE_MIXED.finditer(text or ""):
        index = int(match.group(1))
        if 1 <= index <= len(keys):
            key = keys[index - 1]
            counts[key] = counts.get(key, 0) + 1
    for match in SINGLE_SLOT.finditer(text or ""):
        key = match.group(1)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _min_count(slot: dict[str, Any]) -> int:
    value = slot.get("minCount")
    return 1 if value is None else int(value)


def _is_optional(slot: dict[str, Any]) -> bool:
    """与后端 rules.py::validate_optional_slot_refs **同一句**判据。"""
    return (
        (slot.get("refMode") or "mixed") == "mixed"
        and (slot.get("binding") or "user") != "fixed"
        and _min_count(slot) == 0
    )


def marks_for(slots: list[dict[str, Any]], prompt: str) -> set[str]:
    """这份规格里，哪些槽位可以标成「未选时文案来自框选段」。"""
    keys = visual_keys(slots)
    counts = single_ref_counts(prompt, keys)
    out: set[str] = set()
    for slot in slots:
        key = str(slot.get("key") or "")
        if not _is_optional(slot):
            continue
        if not str(slot.get("emptyText") or "").strip():
            continue
        if counts.get(key, 0) != 1:
            continue
        out.add(key)
    return out


def _apply_to_spec_json(spec: dict[str, Any]) -> list[str]:
    """把一份契约 JSON（草稿 / 案例快照）里够格的槽位标上，返回被改的 key。"""
    slots = spec.get("slots") or []
    prompt = str(spec.get("prompt") or "")
    targets = marks_for(slots, prompt)
    changed: list[str] = []
    for slot in slots:
        key = str(slot.get("key") or "")
        if key in targets and not slot.get("emptyTextFromAnchor"):
            slot["emptyTextFromAnchor"] = True
            changed.append(key)
    return changed


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把老模板的未选时文案认成来自框选段")
    parser.add_argument("--dry-run", action="store_true", help="只看会改什么，不写库")
    args = parser.parse_args(argv)

    factory = get_session_factory()
    changed_slots = 0
    changed_specs = 0
    changed_cases = 0
    skipped: list[str] = []

    async with factory() as session:
        # ---- 已发布版本：槽位行逐条刷
        versions = (
            await session.execute(select(TemplateSpecVersion))
        ).scalars().all()
        rows = (
            await session.execute(select(TemplateSpecSlot))
        ).scalars().all()
        by_version: dict[str, list[Any]] = {}
        for row in rows:
            by_version.setdefault(row.version_id, []).append(row)

        for version in versions:
            slot_rows = by_version.get(version.id, [])
            if not slot_rows:
                continue
            spec_slots = [
                {
                    "key": row.slot_key,
                    "position": row.position,
                    "refMode": row.ref_mode,
                    "binding": row.binding,
                    "minCount": row.min_count,
                    "emptyText": row.empty_text,
                }
                for row in sorted(slot_rows, key=lambda r: (r.position, r.slot_key))
            ]
            targets = marks_for(spec_slots, version.prompt or "")
            for row in slot_rows:
                if row.slot_key in targets and not row.empty_text_from_anchor:
                    changed_slots += 1
                    if not args.dry_run:
                        row.empty_text_from_anchor = True
            for row in slot_rows:
                if row.slot_key not in targets and row.empty_text_from_anchor:
                    skipped.append(f"版本 {version.id} 槽位 {row.slot_key}：已是 true，未动")

        # ---- 草稿（head.draft 是契约 JSON）
        heads = (await session.execute(select(TemplateSpec))).scalars().all()
        for head in heads:
            draft = head.draft or {}
            if not isinstance(draft, dict) or not draft.get("slots"):
                continue
            # **深拷贝**：JSON 列按「序列化后的值」判脏。原地改嵌套 dict 的话，
            # 旧值的快照也被一起改了 → 两边看起来一样 → 根本不会发 UPDATE
            # （踩过一次：改完再读，标记还是 None）。
            draft = json.loads(json.dumps(draft))
            changed = _apply_to_spec_json(draft)
            if changed:
                changed_specs += 1
                print(f"草稿 {head.spec_key}：标记 {len(changed)} 个槽位 {changed}")
                if not args.dry_run:
                    # **赋一个新对象**：JSON 列不追踪「原地修改」，直接写回同一个
                    # dict 会被当成没变 → 第二次跑又改一遍（不幂等）。
                    head.draft = dict(draft)

        # ---- 案例快照（config 与草稿同形）
        cases = (await session.execute(select(TemplateCase))).scalars().all()
        for case in cases:
            config = getattr(case, "config", None)
            if not isinstance(config, dict) or not config.get("slots"):
                continue
            # 同上：深拷贝，避免「值没变」的假象。
            config = json.loads(json.dumps(config))
            changed = _apply_to_spec_json(config)
            if changed:
                changed_cases += 1
                print(f"案例 {case.id}：标记 {len(changed)} 个槽位 {changed}")
                if not args.dry_run:
                    # 同上：JSON 列要换新对象才会被标记为已修改。
                    case.config = dict(config)

        if not args.dry_run:
            await session.commit()

    print()
    print(f"{'（干跑）' if args.dry_run else ''}已发布版本的槽位行：{changed_slots} 条")
    print(f"{'（干跑）' if args.dry_run else ''}草稿：{changed_specs} 份")
    print(f"{'（干跑）' if args.dry_run else ''}案例快照：{changed_cases} 份")
    if skipped:
        print("未动的：")
        for line in skipped[:20]:
            print("  " + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
