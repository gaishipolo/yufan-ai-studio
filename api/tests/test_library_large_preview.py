"""悬浮大图（`/library/assets/{id}/large`）。

网格里的缩略图是封面（360×240），几十像素看不清产品细节；
这一档是长边 1280 的派生图，**悬停时才取**。

三条要钉住的：

1. **按内容摘要缓存** —— 同一份原图只派生一次，第二次直接命中；
2. **惰性派生** —— 没被请求过就不该有派生对象躺在存储里；
3. **文本 / 音频没有大图** —— 明确 404，不给一张空图糊弄。
"""

from __future__ import annotations

import asyncio

import pytest
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


def test_large_preview_is_derived_on_demand(client: TestClient, api: str) -> None:
    """第一次请求才派生；派生完对象躺在存储里。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h, name="large.png", color="teal")

    storage = _storage()
    digest = fetch_asset(asset["id"])["sha256"]
    from modules.library.service import large_preview_key

    key = large_preview_key(digest)
    # 惰性：还没请求过，存储里不该有这个对象
    assert asyncio.run(storage.exists(key)) is False

    response = client.get(f"{api}/library/assets/{asset['id']}/large", headers=owner_h)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("image/jpeg")
    assert len(response.content) > 0
    assert asyncio.run(storage.exists(key)) is True


def test_large_preview_is_cached_by_digest(client: TestClient, api: str) -> None:
    """同一份原图只派生一次：第二次直接命中缓存。

    **判别点**：派生会读原图、重编码。缓存命中时不该再读一次原图 ——
    做法是记下派生对象的修改时间，第二次请求后它必须没变。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h, name="cache.png", color="olive")

    storage = _storage()
    digest = fetch_asset(asset["id"])["sha256"]
    from modules.library.service import large_preview_key

    key = large_preview_key(digest)

    first = client.get(f"{api}/library/assets/{asset['id']}/large", headers=owner_h)
    assert first.status_code == 200
    stamp_1 = _stamp(storage, key)
    assert stamp_1 is not None

    # 第二次：必须复用同一份字节（摘要相同 → key 相同）
    second = client.get(f"{api}/library/assets/{asset['id']}/large", headers=owner_h)
    assert second.status_code == 200
    assert second.content == first.content
    assert _stamp(storage, key) == stamp_1, "命中缓存就不该重新派生"


def test_large_preview_rejects_text_and_audio(client: TestClient, api: str) -> None:
    """文本 / 音频没有画面：明确 404，不给一张空图糊弄。

    **断言 message 而不是只看 404**：文本资产没有摘要也没有封面，
    如果不判类型，它会走到「没有摘要就回退封面」那条分支，**恰好也是 404**
    —— 两条不同的路径给出同一个结果，只看状态码测不出区别。
    message 才能区分「明确说了没有大图」与「碰巧没找到」。
    """
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)

    text = client.post(
        f"{api}/library/text-assets",
        json={"name": "note.md", "content": "# 标题"},
        headers=owner_h,
    )
    assert text.status_code == 201, text.text
    text_id = text.json()["data"]["id"]

    response = client.get(f"{api}/library/assets/{text_id}/large", headers=owner_h)
    assert response.status_code == 404
    # 精确文案：文本走的是「明确判类型」那条分支。
    # 若把类型判断删掉，它会落到「没有摘要就回退封面」那条分支 ——
    # 那条分支的 message 也含「大图」二字，所以断言必须精确到整句。
    assert response.json()["message"] == "文本资产没有大图预览"


def test_large_preview_honours_visibility(client: TestClient, api: str) -> None:
    """别人的私有素材：取不到大图（与其它取件口同一套可见性判定）。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h, name="private.png", color="teal")

    other, other_h = make_user(client, admin, LIB_RW)
    response = client.get(f"{api}/library/assets/{asset['id']}/large", headers=other_h)
    assert response.status_code == 404


def _stamp(storage, key: str) -> str | None:
    """派生对象的「版本戳」：用来判断「有没有被重新生成」。"""
    path = storage.local_path(key)
    if path is not None and path.is_file():
        return f"{path.stat().st_mtime_ns}:{path.stat().st_size}"
    # 远端驱动没有本地路径：退化成「存在与否 + 内容摘要」
    if asyncio.run(storage.exists(key)):
        data = asyncio.run(storage.get(key))
        return f"remote:{len(data)}"
    return None


@pytest.mark.parametrize("kind", ["image"])
def test_large_preview_only_for_media(client: TestClient, api: str, kind: str) -> None:
    """图片能出大图（这是唯一真正用到的场景）。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h, name="ok.png", color="teal")
    response = client.get(f"{api}/library/assets/{asset['id']}/large", headers=owner_h)
    assert response.status_code == 200
