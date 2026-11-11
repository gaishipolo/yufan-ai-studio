"""把已封装的产品视频模板同步到「只有正面主图 + 三视图」的素材包。

## 为什么需要这一步

视频模板在封装时把当时的素材包图片**抄进了 `mediaOrder`**，其中包含细节图。
运行时并不按 mediaOrder 取图（`replacement_service` 是按用途从素材包**当前版本**
取），但 `_check_video_template` 在模板没有声明 `referenceBindings` 时，会把
`mediaOrder` 里的图片用途当成「模板要的槽位」——于是包里一摘掉细节图，这些
老模板就会被判成「当前素材包缺少产品细节图」，谁都跑不了。

所以素材包瘦身之后，模板清单必须跟着瘦：删掉指向已不在包内的图片槽位、
重新编号、把 `productImageCount` 改成实际张数。**提示词一个字都不动**——
被删的永远是没有进提示词的图，`{{Mixed 2}}` / `{{Mixed 3}}` 的语义不变。

## 用法（在 api/ 目录下）

    python scripts/sync_product_template_media.py            # 只读预览
    python scripts/sync_product_template_media.py --apply    # 写回 catalog.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import modules.asset.lookup  # noqa: E402,F401  素材元数据注入口，import 即注册
from db.session import get_session_factory  # noqa: E402
from modules.template import video_template_store as store  # noqa: E402
from modules.template.pack_service import PackService  # noqa: E402
from modules.template.replacement_service import _check_video_template  # noqa: E402
from modules.template.schema import aligned_bindings  # noqa: E402

BACKUP_DIR = Path(__file__).resolve().parents[1] / "var" / "backup"
TOKEN_RE = re.compile(r"\{\{([^{}]+)\}\}")


def _cited_tokens(prompt: str) -> set[str]:
    return {
        f"{{{{{token}}}}}"
        for token in TOKEN_RE.findall(prompt)
        if token != "ReplacementScope"
    }


def _rebuild(
    record: dict, keep_ids: set[str], pack_name: str, pack_version: int
) -> tuple[dict, list[dict]]:
    """按「只留包内还有的图」重建 mediaOrder，并连续重排 token。

    原片不一定在第一位（`referenceBindings` 允许），所以按「原顺序过滤 → 逐项
    重新编号」来，而不是假设细节图永远在最后。逐项 copy，避免改坏原记录。
    """
    media = list(record.get("mediaOrder") or [])
    old_tokens = [item.get("token") or "" for item in media]
    kept_index = [
        index
        for index, item in enumerate(media)
        if item.get("type") == "video" or item.get("assetId") in keep_ids
    ]
    cited = _cited_tokens(record.get("prompt", ""))
    kept: list[dict] = []
    for new_index, old_index in enumerate(kept_index, 1):
        item = dict(media[old_index])
        item["token"] = f"{{{{Mixed {new_index}}}}}"
        item["inPrompt"] = item["token"] in cited
        kept.append(item)

    rules = dict(record.get("rules") or {})
    rules["productImageCount"] = sum(1 for item in kept if item.get("type") == "image")
    rules["promptTokenAllowlist"] = sorted(cited)
    bindings = rules.get("referenceBindings")
    if isinstance(bindings, dict):
        # 旧编号 → 新编号：用途跟着图走，编号按新顺序重排。
        rules["referenceBindings"] = {
            kept[new_index - 1]["token"]: bindings[old_tokens[old_index]]
            for new_index, old_index in enumerate(kept_index, 1)
            if old_tokens[old_index] in bindings
        }
    updated = {
        **record,
        "mediaOrder": kept,
        "rules": rules,
        "packName": pack_name,
        "packVersion": pack_version,
        "updatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    dropped = [
        item
        for index, item in enumerate(media)
        if index not in kept_index and item.get("type") == "image"
    ]
    return updated, dropped


async def run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="写回 catalog.json")
    args = parser.parse_args()

    async with get_session_factory()() as session:
        service = PackService(session)
        published = {pack.id: pack for pack in await service.repo.list_published("product")}
        plan: list[dict] = []
        for record in store._read()["items"]:
            if record.get("defaultKind", "product") != "product":
                continue
            pack = published.get(record.get("packId") or "")
            if pack is None:
                continue
            frozen = await service.repo.get_version(pack.id, pack.version)
            if frozen is None:
                continue
            rows = aligned_bindings(pack.kind, frozen.reference_ids, frozen.bindings)
            roles = {aid: row.role for aid, row in zip(frozen.reference_ids, rows)}
            updated, dropped = _rebuild(
                record, set(frozen.reference_ids), pack.name, pack.version
            )
            if not dropped:
                continue
            # 先验证再落盘：宁可中止，也不要把跑不了的模板写进目录。
            _check_video_template(store._as_out(updated), roles, pack.kind)
            plan.append(
                {
                    "id": record.get("id"),
                    "name": record.get("name"),
                    "pack": f"{pack.name} v{pack.version}",
                    "before": [item.get("name") or item.get("token") for item in record["mediaOrder"]],
                    "after": [
                        f'{item["token"]}={item.get("role") or "source_video"}'
                        for item in updated["mediaOrder"]
                    ],
                    "record": updated,
                }
            )

        view = [{k: v for k, v in row.items() if k != "record"} for row in plan]
        if not args.apply:
            print(
                json.dumps(
                    {"mode": "preflight", "templates": view, "message": "只读预览，未写盘"},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if not plan:
            print(json.dumps({"mode": "apply", "changed": 0}, ensure_ascii=False))
            return 0

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = BACKUP_DIR / f"video-templates-before-sync-{stamp}.json"
        shutil.copy2(store.CATALOG_PATH, backup)
        by_id = {row["id"]: row["record"] for row in plan}
        with store._LOCK:
            data = store._read()
            data["items"] = [by_id.get(item.get("id"), item) for item in data["items"]]
            store._write(data)
        print(
            json.dumps(
                {
                    "mode": "apply",
                    "backup": str(backup),
                    "changed": len(plan),
                    "templates": view,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(run()))
