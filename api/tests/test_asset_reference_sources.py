"""引用检查的「多来源」与归档回收站接线。

上一轮只接了 `video_tasks.reference_asset_ids`，而封装（pack）同样把 `assets.id`
写进自己的文件里。回归的是两个具体形状：

1. 注入口必须能聚合多个来源，不能是「只能注册一个」；
2. 归档素材走的是 `PUT /deleted`（回收站标记），不是 `AssetService.delete_asset`，
   这条路径以前完全没问过「还有谁在用」。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient
from test_asset_api import png_bytes  # noqa: F401
from tests_archive_helpers import archive

from core.ports.assets import (
    ask_asset_references,
    register_asset_reference_checker,
    reset_asset_reference_checker,
)
from infra.archive import get_archive_storage
from modules.task.asset_references import TaskAssetReferences
from modules.template.asset_references import PackAssetReferences


def _restore_defaults() -> None:
    reset_asset_reference_checker()
    register_asset_reference_checker(TaskAssetReferences())
    register_asset_reference_checker(PackAssetReferences())


def _write_pack(key: str, name: str, reference_ids: list[str]) -> None:
    get_archive_storage().write(
        key,
        {
            "id": key.rsplit("/", 1)[-1].removesuffix(".json"),
            "name": name,
            "status": "published",
            "referenceIds": reference_ids,
        },
    )


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


def test_pack_checker_reads_head_and_drafts() -> None:
    """封装的 head 与草稿都算引用；历史版本不算（否则老素材永远删不掉）。"""
    head = "catalog/packs/head-pack.json"
    draft = "catalog/pack_drafts/draft-pack.json"
    version = "catalog/pack_versions/head-pack/1.json"
    storage = get_archive_storage()
    _write_pack(head, "已发布包", ["head-asset"])
    _write_pack(draft, "草稿包", ["draft-asset"])
    _write_pack(version, "历史版本", ["old-asset"])
    try:
        checker = PackAssetReferences(storage)
        result = asyncio.run(
            checker.references(["head-asset", "draft-asset", "old-asset", "none"])
        )
    finally:
        for key in (head, draft, version):
            storage.remove(key)

    assert "已发布包" in " ".join(result["head-asset"])
    assert "草稿" in " ".join(result["draft-asset"])
    assert "old-asset" not in result


def test_archive_trash_blocked_by_published_pack(client: TestClient, api: str) -> None:
    """把已发布封装用到的原片移进回收站，必须 409 并说出是哪个包。"""
    asset = archive.upload(client, "pack-cover.png", "product", png_bytes((20, 20), "red"))
    key = "catalog/packs/christmas-pack.json"
    _write_pack(key, "圣诞活动主图", [asset["id"]])
    try:
        response = client.put(
            f"{api}/assets/archive/{asset['id']}/deleted",
            json={"expectedVersion": asset["stateVersion"], "deleted": True},
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == 40951
        assert "圣诞活动主图" in str(response.json()["details"])

        # 拦截不能是「改了一半」：库里仍是未删除状态
        detail = client.get(f"{api}/assets/archive/{asset['id']}")
        assert detail.status_code == 200
        assert detail.json()["data"]["deletedAt"] is None
    finally:
        get_archive_storage().remove(key)


def test_restore_from_trash_does_not_need_reference_check(client: TestClient, api: str) -> None:
    """恢复是「把东西放回」，不是删除：不该被引用检查挡住。"""
    asset = archive.upload(client, "restore.png", "product", png_bytes((20, 20), "blue"))
    base = {"expectedVersion": asset["stateVersion"], "deleted": True}
    removed = client.put(f"{api}/assets/archive/{asset['id']}/deleted", json=base)
    assert removed.status_code == 200, removed.text

    restored = client.put(
        f"{api}/assets/archive/{asset['id']}/deleted",
        json={"expectedVersion": removed.json()["data"]["stateVersion"], "deleted": False},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["deletedAt"] is None
