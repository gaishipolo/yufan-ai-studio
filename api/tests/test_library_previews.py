"""缩略图对账：原图在、封面对象没了，`library repreview` 要能修回来。

这一条针对的是「记录在、字节不在」里最难解释的一种：原图按 `sha256` 落在
`library/blobs/`，封面按同一摘要派生到 `library/previews/`——**两套字节一条记录**。
封面被清掉（换桶、MinIO 重建、当年抽帧失败的残留）时，原图完好，但列表只剩
渐变占位、详情页一个裂图标，用户只会问「素材是不是丢了」。

用例先把封面真的删掉，再确认修复命令能重建出同名对象；试运行必须一个字节都不写。
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from library_support import (
    LIB_RW,
    admin_headers,
    fetch_asset,
    make_user,
    upload_image,
)


def _storage():
    from infra import get_storage

    return get_storage()


def test_repreview_rebuilds_a_missing_cover(client: TestClient, api: str) -> None:
    from modules.library.maintenance import run_preview_repair

    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h, name="cover-lost.png", color="khaki")

    storage = _storage()
    cover_key = fetch_asset(asset["id"])["cover_key"]
    assert cover_key, "上传必须有封面，否则这条用例没测到东西"

    # 模拟「封面对象没了」：只删封面，原图不动
    asyncio.run(storage.delete(cover_key))
    assert asyncio.run(storage.exists(cover_key)) is False
    assert asyncio.run(storage.exists(fetch_asset(asset["id"])["storage_key"])) is True

    dry = asyncio.run(run_preview_repair(owner=owner["id"], dry_run=True))
    assert dry.scanned == 1
    assert dry.pending == 1
    assert asyncio.run(storage.exists(cover_key)) is False, "试运行不能写盘"

    report = asyncio.run(run_preview_repair(owner=owner["id"]))
    assert report.rebuilt == 1
    assert report.broken == 0

    rebuilt_key = fetch_asset(asset["id"])["cover_key"]
    assert rebuilt_key == cover_key, "封面按摘要派生，重建后 key 不该变"
    assert asyncio.run(storage.exists(rebuilt_key)) is True

    # 再跑一次是幂等的：已经是好的就不再算进「需要重建」
    again = asyncio.run(run_preview_repair(owner=owner["id"]))
    assert again.ok == 1
    assert again.rebuilt == 0


def test_repreview_skips_audio_and_pack(client: TestClient, api: str) -> None:
    """音频没有封面、封装的封面是**借**成员的（自己的 `sha256` 为空）。

    给它们重建会往 `previews/<空摘要>` 写图，或者把成员的图算到封装头上——
    扫描范围必须收在 `image` / `video`。
    """
    from modules.library.maintenance import run_preview_repair

    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    member = upload_image(client, api, owner_h, name="repack.png", color="teal")
    pack = client.post(
        f"{api}/library/packs",
        json={"name": "不扫的封装", "assetIds": [member["id"]]},
        headers=owner_h,
    )
    assert pack.status_code == 201, pack.text

    report = asyncio.run(run_preview_repair(owner=owner["id"], dry_run=True))
    # 只数到那张图片成员；封装与（没有的）音频都不在扫描范围里
    assert report.scanned == 1
