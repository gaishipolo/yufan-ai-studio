"""显示名与格式后缀：改名不能改格式，列表要带标签。

两条都是产品口径，不是实现细节：

1. **格式由文件决定**。库里只存两套字节（原图 + 缩略图），`kind` / `mime_type`
   在入库那一刻由真实文件定死。如果改名能把 `.png` 改成 `.mp4`，就会出现
   「徽标说图片、下载下来叫 .mp4、播放器打不开」的鬼东西。所以改名只改主体，
   后缀拼回记录自己的。
2. **标签要跟着列表走**。卡片要在体积下方显示标签，不能为了几个字让每张卡
   再发一次详情请求。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from library_support import (
    LIB_RW,
    admin_headers,
    make_user,
    upload_image,
)


def test_rename_cannot_turn_an_image_into_a_video(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h, name="产品图.png")

    renamed = client.patch(
        f"{api}/library/assets/{asset['id']}", json={"name": "换个名字.mp4"}, headers=owner_h
    )
    assert renamed.status_code == 200
    data = renamed.json()["data"]
    assert data["name"] == "换个名字.png", "用户输入的后缀不该生效"
    assert data["kind"] == "image"
    assert data["mimeType"] == "image/png"

    # 不带后缀也补回原格式：否则下载下来是个没有扩展名的文件
    bare = client.patch(
        f"{api}/library/assets/{asset['id']}", json={"name": "只有主体"}, headers=owner_h
    )
    assert bare.json()["data"]["name"] == "只有主体.png"


def test_rename_keeps_dots_that_are_part_of_the_name(client: TestClient, api: str) -> None:
    """`v1.2` 里的 `.2` 不是扩展名，剥掉就成了 `v1`——比不改名更糟。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    asset = upload_image(client, api, owner_h, name="封面.png")

    for requested, expected in [
        ("v1.2", "v1.2.png"),
        ("主图-2.0 终稿", "主图-2.0 终稿.png"),
        ("a.b.png", "a.b.png"),
    ]:
        response = client.patch(
            f"{api}/library/assets/{asset['id']}", json={"name": requested}, headers=owner_h
        )
        assert response.status_code == 200
        assert response.json()["data"]["name"] == expected


def test_pack_rename_gets_no_suffix(client: TestClient, api: str) -> None:
    """封装没有字节，不该被塞一个 `.png`。"""
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW)
    first = upload_image(client, api, owner_h, name="a.png", color="green")
    second = upload_image(client, api, owner_h, name="b.png", color="navy")

    pack = client.post(
        f"{api}/library/packs",
        json={"name": "春季素材包", "assetIds": [first["id"], second["id"]]},
        headers=owner_h,
    )
    assert pack.status_code == 201, pack.text
    pack_id = pack.json()["data"]["id"]

    renamed = client.patch(
        f"{api}/library/assets/{pack_id}", json={"name": "夏季素材包"}, headers=owner_h
    )
    assert renamed.status_code == 200
    assert renamed.json()["data"]["name"] == "夏季素材包"


def test_list_items_carry_their_tags(client: TestClient, api: str) -> None:
    admin = admin_headers(client)
    owner, owner_h = make_user(client, admin, LIB_RW + ["library:tag:manage"])
    asset = upload_image(client, api, owner_h)
    other = upload_image(client, api, owner_h, color="teal", name="另一张.png")

    tags = [
        client.post(f"{api}/library/tags", json={"name": name}, headers=owner_h).json()["data"]
        for name in ("圣诞活动", "Banner")
    ]
    patched = client.patch(
        f"{api}/library/assets/{asset['id']}",
        json={"tagIds": [tag["id"] for tag in tags]},
        headers=owner_h,
    )
    assert patched.status_code == 200, patched.text

    page = client.get(f"{api}/library/assets", params={"pageSize": 50}, headers=owner_h).json()[
        "data"
    ]
    by_id = {item["id"]: item for item in page["items"]}
    # 按标签名排序，与详情页同一口径；没打标签的给空数组，不是缺键
    assert [tag["name"] for tag in by_id[asset["id"]]["tags"]] == ["Banner", "圣诞活动"]
    assert by_id[other["id"]]["tags"] == []

    detail = client.get(f"{api}/library/assets/{asset['id']}", headers=owner_h).json()["data"]
    assert detail["tags"] == by_id[asset["id"]]["tags"], "列表与详情的标签必须一致"
