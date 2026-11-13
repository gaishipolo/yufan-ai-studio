"""引用检查的「多来源」与归档回收站接线。

上一轮只接了 `video_tasks.reference_asset_ids`，而封装（pack）同样把 `assets.id`
写进自己的数据里。回归的仍是两个形状：

1. 注入口必须能聚合多个来源，不能是「只能注册一个」；
2. 归档素材走的是 `PUT /deleted`（回收站标记），不是 `AssetService.delete_asset`，
   这条路径以前完全没问过「还有谁在用」。

重建后第 2 点的数据源从「扫 `catalog/packs/*.json`」变成
「join `template_pack_references`」，所以断言从「读 JSON 里有名字」变成
「插一行引用后删除被拦住」——行为一样，现场变了。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient
from tests_template_helpers import add_pack, drop_pack, png_bytes, upload

from core.ports.assets import (
    ask_asset_references,
    register_asset_reference_checker,
    reset_asset_reference_checker,
)
from modules.template.asset_references import PackAssetReferences


def _restore_defaults() -> None:
    # 任务模块的参考图已切到 v2（`library_references`），v1 这里只剩封装一个真实来源
    reset_asset_reference_checker()
    register_asset_reference_checker(PackAssetReferences())


def test_ask_merges_all_sources() -> None:
    """一个来源说「有人用」就够拒绝，描述要合并、不能互相覆盖。"""

    class First:
        async def references(self, asset_ids: Any) -> dict:
            return {"a": ["任务《A》"]}

    class Second:
        async def references(self, asset_ids: Any) -> dict:
            return {"a": ["封装《B》"], "b": ["封装《C》"]}

    reset_asset_reference_checker()
    register_asset_reference_checker(First())
    register_asset_reference_checker(Second())
    try:
        result = asyncio.run(ask_asset_references(["a", "b", "none"]))
    finally:
        _restore_defaults()

    assert result["a"] == ["任务《A》", "封装《B》"]
    assert result["b"] == ["封装《C》"]
    assert "none" not in result


async def test_pack_checker_reads_head_only() -> None:
    """封装的 head 算引用；历史版本不算（否则老素材永远删不掉）。

    历史版本是冻结证据，每次发布都留一份——把它算成引用，一个用了半年的封面包
    会让它引用过的每一张图都删不掉，包括早就换掉的那些。
    """
    pack = await add_pack(name="已发布包", reference_ids=["head-asset"])
    archived = await add_pack(
        name="归档草稿",
        reference_ids=["draft-asset"],
        status="draft",
    )
    try:
        result = await PackAssetReferences().references(
            ["head-asset", "draft-asset", "old-asset", "none"]
        )
        assert "已发布包" in " ".join(result["head-asset"])
        assert "归档草稿" in " ".join(result["draft-asset"])
        assert "old-asset" not in result, "历史版本不该阻止删除"
        assert archived.id, "草稿也要能被查到（它同样把 assets.id 写进了引用表）"
    finally:
        await drop_pack(pack.id)
        await drop_pack(archived.id)


async def test_soft_deleted_pack_does_not_block(client: TestClient) -> None:
    """删掉（软删）的封装不算引用：它自己都不可见了，不该拦住素材清理。"""
    asset = upload(client, "only-ref.png", "character", png_bytes())
    pack = await add_pack(name="要删的封装", reference_ids=[asset["id"]])
    try:
        assert asset["id"] in (await PackAssetReferences().references([asset["id"]]))
    finally:
        await drop_pack(pack.id)
    assert asset["id"] not in (await PackAssetReferences().references([asset["id"]]))


def test_archive_trash_blocked_by_published_pack(client: TestClient) -> None:
    """把封装用到的原片移进回收站，必须 409 并说出是哪个包。"""
    asset = upload(client, "pack-cover.png", "product", png_bytes())
    pack = asyncio.run(add_pack(name="圣诞活动主图", kind="product", reference_ids=[asset["id"]]))
    try:
        response = client.put(
            f"/api/v1/assets/{asset['id']}/deleted",
            json={"expectedVersion": asset["stateVersion"], "deleted": True},
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == 40951
        assert "圣诞活动主图" in str(response.json()["details"])

        # 拦截不能是「改了一半」：库里仍是未删除状态
        detail = client.get(f"/api/v1/assets/{asset['id']}")
        assert detail.status_code == 200
        assert detail.json()["data"]["deletedAt"] is None
    finally:
        asyncio.run(drop_pack(pack.id))


def test_restore_from_trash_does_not_need_reference_check(client: TestClient) -> None:
    """恢复是「把东西放回」，不是删除：不该被引用检查挡住。"""
    asset = upload(client, "restore.png", "product", png_bytes())
    base = {"expectedVersion": asset["stateVersion"], "deleted": True}
    removed = client.put(f"/api/v1/assets/{asset['id']}/deleted", json=base)
    assert removed.status_code == 200, removed.text

    restored = client.put(
        f"/api/v1/assets/{asset['id']}/deleted",
        json={"expectedVersion": removed.json()["data"]["stateVersion"], "deleted": False},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["deletedAt"] is None
