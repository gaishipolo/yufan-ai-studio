"""资产库 v2 上传链路（M1）：上传、秒传、幂等、列表、取件、隔离。

刻意覆盖的失败路径不少于成功路径：类型伪装、空文件、跨类型上传都是
「不写在这儿就等于没有」的校验；成功路径反而最容易碰巧通过。

测试库是 session 级共享的，所以断言一律按 **id 查得到**，不断言精确总数——
前面用例上传的资产会留在库里，`total == 1` 这种断言第二天就会红。
"""

from __future__ import annotations

import io

from fastapi.testclient import TestClient
from PIL import Image


def png_bytes(size: tuple[int, int] = (40, 60), color: str = "purple") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _upload(
    client: TestClient,
    api: str,
    content: bytes,
    *,
    filename: str = "cover.png",
    kind: str = "image",
    source_key: str | None = None,
):
    params = {"filename": filename, "kind": kind}
    if source_key:
        params["sourceKey"] = source_key
    return client.post(f"{api}/library/assets/upload", content=content, params=params)


def test_upload_image_and_read_it_back(client: TestClient, api: str) -> None:
    content = png_bytes(color="orange")
    response = _upload(client, api, content)
    assert response.status_code == 201, response.text
    asset = response.json()["data"]
    assert asset["kind"] == "image"
    assert asset["state"] == "active"
    assert asset["visibility"] == "personal"
    assert asset["sizeBytes"] == len(content)
    assert asset["durationSec"] is None
    # 图片必须生成缩略图：列表用 <img> 直接取它
    assert asset["coverUrl"]

    listed = client.get(f"{api}/library/assets").json()["data"]
    assert any(item["id"] == asset["id"] for item in listed["items"])
    assert listed["facets"]["personal"] >= 1

    detail = client.get(f"{api}/library/assets/{asset['id']}").json()["data"]
    assert detail["generation"] is None
    assert detail["tags"] == []

    original = client.get(f"{api}/library/assets/{asset['id']}/file")
    assert original.status_code == 200
    assert original.content == content

    thumbnail = client.get(f"{api}/library/assets/{asset['id']}/thumbnail")
    assert thumbnail.status_code == 200
    assert thumbnail.headers["content-type"] == "image/jpeg"


def test_same_content_is_uploaded_once(client: TestClient, api: str) -> None:
    content = png_bytes(color="teal")
    first = _upload(client, api, content).json()["data"]
    second = _upload(client, api, content, filename="same.png").json()["data"]
    assert first["id"] == second["id"], "同 owner 同内容同类型必须秒传回同一条"

    listed = client.get(f"{api}/library/assets", params={"pageSize": 100}).json()["data"]
    same_id = [item for item in listed["items"] if item["id"] == first["id"]]
    assert len(same_id) == 1


def test_source_key_is_idempotent_before_dedup(client: TestClient, api: str) -> None:
    """同 sourceKey、不同内容 → 第二次返回第一次的 id，不新建行。

    这条比「内容去重」更严格：生成侧的轮询会重复回调，两次回调之间文件
    可能被重新编码（字节不同、语义同一）。幂等键优先于摘要。
    """
    key = "image:job-42:0"
    first = _upload(client, api, png_bytes(color="navy"), source_key=key).json()["data"]
    second = _upload(
        client, api, png_bytes(size=(41, 61), color="navy"), source_key=key
    ).json()["data"]
    assert first["id"] == second["id"]


def test_upload_rejects_wrong_kind(client: TestClient, api: str) -> None:
    response = _upload(client, api, png_bytes(), kind="video")
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == 40082, "v2 的类型错误码必须是 slot 8 的 40082"


def test_upload_rejects_empty_body(client: TestClient, api: str) -> None:
    response = _upload(client, api, b"")
    assert response.status_code == 400


def test_upload_rejects_unsupported_extension(client: TestClient, api: str) -> None:
    response = _upload(client, api, b"not an image", filename="notes.txt", kind="image")
    assert response.status_code == 400
    assert response.json()["code"] == 40082


def test_source_videos_only_accepts_video(client: TestClient, api: str) -> None:
    response = client.post(
        f"{api}/library/source-videos",
        content=png_bytes(),
        params={"filename": "clip.png"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == 40082


def test_unknown_asset_is_404(client: TestClient, api: str) -> None:
    assert client.get(f"{api}/library/assets/does-not-exist").status_code == 404


