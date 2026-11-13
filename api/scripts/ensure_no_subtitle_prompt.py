"""检查全部视频模板提示词是否带「全片无字幕」，缺的按统一格式补上。

## 为什么要有这个约束

平台侧对字幕的处理是「移除 + 不新增」，但只写「移除全部原字幕」在部分模型上
会残留半截字幕或把原字幕当画面纹理重绘。显式在**开头**写一句「全片无字幕」，
比末尾那句「移除全部原字幕」更像硬约束——两条都要有。

## 为什么走接口而不是直接改库

`PATCH /templates/video-templates/{id}/prompt` 是唯一的改写入口：它按新原文重算
`{{Mixed N}}` 引用、保持未引用的槽位不缩短、并写一条 `update_prompt` 审计。
直接 `UPDATE video_templates SET prompt = ...` 会绕过这些，且不会留下
「谁在什么时候改了哪份提示词」。

**盘点读数据库**：模板目录落到 `video_templates` 表之后，这里和服务端读的是
同一份数据，不再存在「盘点了一台机器的 `catalog.json`、却去改另一台」的老问题。

## 空结果不等于合规

表里一条都没有时**退出码 2**：一个连错库或还没导入存量数据的盘点，
最不该给的就是「全部提示词都带全片无字幕」这个最令人放心的结论。

## 用法（在 api/ 目录下）

    python scripts/ensure_no_subtitle_prompt.py            # 只读盘点
    python scripts/ensure_no_subtitle_prompt.py --apply    # 补齐

令牌从 `YUFAN_API_TOKEN` 读取；只在缺失时提交，重复执行是幂等的。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402

MARKER = "全片无字幕"
#: 补在开头；带句号是因为它是一句完整的话，后面再接正文。
PREFIX = "全片无字幕。\n"


def _already(text: str) -> bool:
    return MARKER in text


async def _load_templates() -> list[dict[str, Any]]:
    """只读整表：盘点要的是「全部模板」，这条命令不在请求路径上。"""
    from sqlalchemy import select

    from db.session import get_session_factory
    from modules.template.models import VideoTemplate

    async with get_session_factory()() as session:
        rows = (await session.scalars(select(VideoTemplate))).all()
        return [
            {
                "id": row.id,
                "name": row.name,
                "prompt": row.prompt or "",
                "packId": row.pack_id,
                "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in rows
        ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="补齐缺失的提示词")
    parser.add_argument("--api-url", default="http://127.0.0.1:8080/api/v1")
    parser.add_argument(
        "--backup-dir",
        default="",
        help="改写前的整体快照落点（默认 api/var/video_templates/backup）",
    )
    args = parser.parse_args(argv)

    try:
        items = asyncio.run(_load_templates())
    except Exception as exc:  # noqa: BLE001 - 连不上库要给人话，不是 traceback
        print(
            f"读不到视频模板表：{type(exc).__name__}: {exc}\n"
            "这不是「全部合规」，而是这份盘点根本没读到东西；请确认 DB_URL 指向服务端那个库。",
            file=sys.stderr,
        )
        return 2
    if not items:
        print(
            "视频模板表里一条记录都没有。\n"
            "这不是「全部合规」，而是这份盘点根本没读到东西；"
            "存量数据需要先跑 `app import-video-templates`。",
            file=sys.stderr,
        )
        return 2

    todo = [item for item in items if not _already(item["prompt"])]
    report: dict[str, Any] = {
        "mode": "apply" if args.apply else "preflight",
        "total": len(items),
        "missing": [
            {"id": item["id"], "name": item["name"], "chars": len(item["prompt"])}
            for item in todo
        ],
    }
    if not todo:
        print(
            json.dumps(
                {**report, "message": "全部提示词都带「全片无字幕」"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not args.apply:
        print(json.dumps({**report, "message": "只读盘点，未写入"}, ensure_ascii=False, indent=2))
        return 0

    token = os.environ.get("YUFAN_API_TOKEN", "")
    if not token:
        print("请设置 YUFAN_API_TOKEN 环境变量", file=sys.stderr)
        return 1
    # 改写前留一份整体快照：提示词是逐字资产，改坏了要能一眼看出原样。
    # 落点默认仍是 `api/var/video_templates/backup`（与旧目录文件同级的习惯位置），
    # 共享盘部署时用 `--backup-dir` 指过去。
    backup_dir = (
        Path(args.backup_dir).expanduser()
        if args.backup_dir
        else Path(__file__).resolve().parents[1] / "var" / "video_templates" / "backup"
    )
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    backup = backup_dir / f"video-templates-{stamp}-pre-subtitle.json"
    backup.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    report["backup"] = str(backup)
    # 不继承进程里的代理与 TLS 调试变量，行为与封装器一致。
    with httpx.Client(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(60, connect=10),
        trust_env=False,
    ) as http:
        results = []
        for item in todo:
            before = item["prompt"]
            response = http.patch(
                f"{args.api_url}/templates/video-templates/{item['id']}/prompt",
                json={"prompt": PREFIX + before},
            )
            if not response.is_success:
                results.append(
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "status": f"失败 HTTP {response.status_code}",
                    }
                )
                continue
            after = (response.json().get("data") or {}).get("prompt") or ""
            results.append(
                {
                    "id": item["id"],
                    "name": item["name"],
                    "status": "已补齐",
                    "chars": f"{len(before)} → {len(after)}",
                    "prefixOk": after.startswith(PREFIX.rstrip("\n")),
                }
            )
    print(json.dumps({**report, "results": results}, ensure_ascii=False, indent=2))
    return 0 if all(row["status"] == "已补齐" for row in results) else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
