"""检查全部视频模板提示词是否带「全片无字幕」，缺的按统一格式补上。

## 为什么要有这个约束

平台侧对字幕的处理是「移除 + 不新增」，但只写「移除全部原字幕」在部分模型上
会残留半截字幕或把原字幕当画面纹理重绘。显式在**开头**写一句「全片无字幕」，
比末尾那句「移除全部原字幕」更像硬约束——两条都要有。

## 为什么走接口而不是改目录

`PATCH /templates/video-templates/{id}/prompt` 是唯一的改写入口：它按新原文重算
`{{Mixed N}}` 引用、保持未引用的槽位不缩短、并写一条 `update_prompt` 审计。
直接改 `catalog.json` 会绕过这些，且不会留下「谁在什么时候改了哪份提示词」。

**盘点例外**：待补清单是直接读 `VIDEO_TEMPLATE_CATALOG` 指向的那份文件的（只读），
所以它必须与服务端使用**同一份**目录——脚本会把实际读取的路径打进报告，
共享盘部署时先核对这一行，别盘点了一台机器的副本却去改另一台。

改写前的快照写在目录文件同级的 `backup/`（与 `video_template_store` 的写前快照
同一套滚动规则，保留最近 20 份），不落在本机 `api/var`。

## 用法（在 api/ 目录下）

    python scripts/ensure_no_subtitle_prompt.py            # 只读盘点
    python scripts/ensure_no_subtitle_prompt.py --apply    # 补齐

令牌从 `YUFAN_API_TOKEN` 读取；只在缺失时提交，重复执行是幂等的。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402

from modules.template import video_template_store as store  # noqa: E402

MARKER = "全片无字幕"
#: 补在开头；带句号是因为它是一句完整的话，后面再接正文。
PREFIX = "全片无字幕。\n"


def _already(text: str) -> bool:
    return MARKER in text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="补齐缺失的提示词")
    parser.add_argument("--api-url", default="http://127.0.0.1:8080/api/v1")
    args = parser.parse_args(argv)

    catalog = store.CATALOG_PATH
    if not catalog.is_file():
        # `_read()` 对「文件不存在」和「文件里没有条目」都返回空 items，
        # 直接往下走会报出「全部提示词都带全片无字幕」——一个路径配错或共享盘
        # 没挂上的运维脚本，最不该给的就是这个最令人放心的结论。
        print(
            f"模板目录文件不存在：{catalog}\n"
            "这不是「全部合规」，而是这份盘点根本没读到东西；"
            "请确认 VIDEO_TEMPLATE_CATALOG 指向服务端实际使用的那一份。",
            file=sys.stderr,
        )
        return 2
    items = store._read()["items"]
    todo = [item for item in items if not _already(item.get("prompt") or "")]
    report = {
        "mode": "apply" if args.apply else "preflight",
        "catalog": str(catalog),
        "total": len(items),
        "missing": [
            {"id": item.get("id"), "name": item.get("name"), "chars": len(item.get("prompt") or "")}
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
    # 改写前先留一份目录快照：提示词是逐字资产，改坏了要能一眼看出原样。
    # 快照必须跟着目录文件走——目录可能在共享盘上（`VIDEO_TEMPLATE_CATALOG`），
    # 备份落回本机 `api/var` 的话，换台机器就找不到这份「改之前的样子」。
    # 命名沿用 `catalog-<stamp>` 前缀，与 `video_template_store._backup_current`
    # 共用「保留最近 20 份」的滚动规则（时间戳定宽，排序仍然按时间）。
    backup_dir = catalog.parent / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    backup = backup_dir / f"catalog-{stamp}-pre-subtitle.json"
    backup.write_text(
        json.dumps(store._read(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report["backup"] = str(backup)
    # 不继承进程里的代理与 TLS 调试变量，行为与封装器一致。
    with httpx.Client(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(60, connect=10),
        trust_env=False,
    ) as http:
        results = []
        for item in todo:
            before = item.get("prompt") or ""
            response = http.patch(
                f"{args.api_url}/templates/video-templates/{item['id']}/prompt",
                json={"prompt": PREFIX + before},
            )
            if not response.is_success:
                results.append(
                    {
                        "id": item["id"],
                        "name": item.get("name"),
                        "status": f"失败 HTTP {response.status_code}",
                    }
                )
                continue
            after = (response.json().get("data") or {}).get("prompt") or ""
            results.append(
                {
                    "id": item["id"],
                    "name": item.get("name"),
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
