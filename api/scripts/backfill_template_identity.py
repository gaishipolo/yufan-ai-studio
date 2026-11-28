"""按任务名回填旧生成记录的模板身份（一次性修补）。

2026-11-28 新增 template_key/version/name 三列后，历史任务与生图行都是 NULL。
本脚本按**任务名 == 模板名**做一次尽力回填：

- 只考虑 `current_version > 0` 的模板（从没发布过的模板不可能被模板库使用）；
- 同名只有一个模板 → 直接匹配；
- 同名多个 → 若只有一个是该记录使用人的模板，取它；否则**跳过并报告歧义**；
- 匹配成功写入 `template_key` / `template_name` / `template_version`，版本按
  用户口径取模板**当前最新发布版本**（历史真实版本已不可考）；
- 不匹配的保持 NULL：视频视为「生成工作台」、生图视为「生图」直接来源。

## 为什么必须报告歧义

模板名不是唯一键（不同作者可以同名）。猜错会把 A 的使用量记到 B 头上，
而且这种错不会在页面上显形。宁可少修，不制造错账。

## 幂等与回滚

只处理 `template_key IS NULL` 的行；重复跑不会覆盖已有身份。实跑时把每一条
改动写到 `var/backups/template-identity-<时间>.json`，需要回滚时按 id 把三列
置回 NULL 即可。

用法：

    python scripts/backfill_template_identity.py --dry-run
    python scripts/backfill_template_identity.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from sqlalchemy import select  # noqa: E402

from db.session import get_session_factory  # noqa: E402
from modules.image.models import ImageJob  # noqa: E402
from modules.task.models import VideoTask  # noqa: E402
from modules.template_pack.models import TemplateSpec  # noqa: E402


@dataclass(frozen=True, slots=True)
class Choice:
    key: str
    name: str
    version: int


def _norm(value: str | None) -> str:
    return " ".join((value or "").split())


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def choose_template(
    candidates: Sequence[TemplateSpec],
    owner_id: str,
    used_at: datetime,
) -> tuple[Choice | None, str]:
    """同名候选里选一个；选不出时返回 `(None, 原因)`。

    先排掉「模板创建晚于任务」的候选：那种模板当时还不存在，不可能是来源。
    """
    existed = [row for row in candidates if _utc(row.created_at) <= _utc(used_at)]
    if not existed:
        return None, "no-match"
    if len(existed) == 1:
        row = existed[0]
        return Choice(row.spec_key, row.name, row.current_version), "unique"
    same_owner = [row for row in existed if row.owner_id == owner_id]
    if len(same_owner) == 1:
        row = same_owner[0]
        return Choice(row.spec_key, row.name, row.current_version), "owner"
    return None, "ambiguous"


async def run(*, dry_run: bool, report: Path | None) -> int:
    factory = get_session_factory()
    changes: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    counts = {
        "templates": 0,
        "video_total": 0,
        "video_matched": 0,
        "video_unmatched": 0,
        "image_total": 0,
        "image_matched": 0,
        "image_unmatched": 0,
    }

    async with factory() as session:
        templates = (await session.scalars(select(TemplateSpec))).all()
        usable = [row for row in templates if row.current_version > 0]
        counts["templates"] = len(usable)
        by_name: dict[str, list[TemplateSpec]] = {}
        for row in usable:
            name = _norm(row.name)
            if name:
                by_name.setdefault(name, []).append(row)

        videos = (
            await session.scalars(
                select(VideoTask).where(VideoTask.template_key.is_(None))
            )
        ).all()
        images = (
            await session.scalars(
                select(ImageJob).where(ImageJob.template_key.is_(None))
            )
        ).all()

        for row in videos:
            counts["video_total"] += 1
            name = _norm(row.title)
            choice, reason = choose_template(
                by_name.get(name, ()), row.author_id, row.created_at
            )
            if choice is None:
                if reason == "ambiguous":
                    ambiguous.append(
                        {
                            "source": "video",
                            "id": row.id,
                            "name": name,
                            "candidates": [
                                {
                                    "key": candidate.spec_key,
                                    "ownerId": candidate.owner_id,
                                }
                                for candidate in by_name.get(name, ())
                            ],
                        }
                    )
                else:
                    counts["video_unmatched"] += 1
                continue
            counts["video_matched"] += 1
            changes.append(
                {
                    "source": "video",
                    "id": row.id,
                    "name": name,
                    "templateKey": choice.key,
                    "templateName": choice.name,
                    "templateVersion": choice.version,
                }
            )
            if not dry_run:
                row.template_key = choice.key
                row.template_name = choice.name
                row.template_version = choice.version

        for row in images:
            counts["image_total"] += 1
            name = _norm(row.name)
            choice, reason = choose_template(
                by_name.get(name, ()), row.owner_id, row.created_at
            )
            if choice is None:
                if reason == "ambiguous":
                    ambiguous.append(
                        {
                            "source": "image",
                            "id": row.id,
                            "name": name,
                            "candidates": [
                                {
                                    "key": candidate.spec_key,
                                    "ownerId": candidate.owner_id,
                                }
                                for candidate in by_name.get(name, ())
                            ],
                        }
                    )
                else:
                    counts["image_unmatched"] += 1
                continue
            counts["image_matched"] += 1
            changes.append(
                {
                    "source": "image",
                    "id": row.id,
                    "name": name,
                    "templateKey": choice.key,
                    "templateName": choice.name,
                    "templateVersion": choice.version,
                }
            )
            if not dry_run:
                row.template_key = choice.key
                row.template_name = choice.name
                row.template_version = choice.version

        if not dry_run:
            await session.commit()

    print(f"可用模板（current_version > 0）：{counts['templates']}")
    print(
        "视频："
        f"待查 {counts['video_total']}，匹配 {counts['video_matched']}，"
        f"无模板 {counts['video_unmatched']}"
    )
    print(
        "生图："
        f"待查 {counts['image_total']}，匹配 {counts['image_matched']}，"
        f"无模板 {counts['image_unmatched']}"
    )
    print(f"同名歧义（未修改）：{len(ambiguous)}")
    for item in ambiguous[:10]:
        keys = "、".join(candidate["key"] for candidate in item["candidates"])
        print(f"  - {item['source']} {item['id']}「{item['name']}」候选：{keys}")
    if len(ambiguous) > 10:
        print(f"  ……还有 {len(ambiguous) - 10} 条")

    if dry_run:
        print(f"（dry-run：将修改 {len(changes)} 条，未写库）")
        return 0

    target = report or (
        API_ROOT
        / "var"
        / "backups"
        / f"template-identity-{datetime.now(tz=UTC).strftime('%Y%m%d-%H%M%S')}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "generatedAt": datetime.now(tz=UTC).isoformat(),
                "changes": changes,
                "ambiguous": ambiguous,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"已修改 {len(changes)} 条；回滚清单：{target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按名称回填历史生成记录的模板身份")
    parser.add_argument("--dry-run", action="store_true", help="只看会改什么，不写库")
    parser.add_argument("--report", type=Path, default=None, help="实跑时的回滚清单路径")
    args = parser.parse_args(argv)
    return asyncio.run(run(dry_run=args.dry_run, report=args.report))


if __name__ == "__main__":
    raise SystemExit(main())