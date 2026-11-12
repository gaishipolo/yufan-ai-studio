"""文本资产（方案 B）：站内新建、在线编辑、版本历史、上传与取件。

这里覆盖的是「一条资产行 + 一张不可变版本表」的边界，重点不是 CRUD 本身，
而是几条会静默出错的口径：

- 正文一律纯文本：BOM 会被剥、非 UTF-8 直接拒、`.md` 取件仍是 `text/plain`；
- 编辑是追加一版，恢复也是追加一版，历史行永远不改写；
- 文本没有缩略图，`coverUrl` 必须是 null，否则前端每张卡一次 404；
- 文本接口只认 `kind=text`，拿图片资产调不得撞出半个文本资产。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from library_support import admin_headers, png_bytes, suffix

API = "/api/v1"


def _headers(client: TestClient) -> dict[str, str]:
    return admin_headers(client)


def _create(client: TestClient, headers: dict[str, str], content: str) -> dict[str, Any]:
    response = client.post(
        f"{API}/library/text-assets",
        json={"name": f"文案{suffix()}", "content": content, "extension": "md"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _upload(
    client: TestClient,
    headers: dict[str, str],
    content: bytes,
    *,
    filename: str = "note.md",
    kind: str = "text",
) -> Any:
    return client.post(
        f"{API}/library/assets/upload",
        content=content,
        params={"filename": filename, "kind": kind},
        headers=headers,
    )


# --------------------------------------------------------------------------- 站内新建 / 版本


def test_create_edit_conflict_versions_and_restore(client: TestClient, api: str) -> None:
    headers = _headers(client)
    created = _create(client, headers, "第一版正文\n第二行")
    asset_id = created["id"]

    assert created["name"].endswith(".md")
    assert created["version"] == 1
    assert created["content"] == "第一版正文\n第二行"
    assert created["sizeBytes"] == len("第一版正文\n第二行".encode())

    read = client.get(f"{api}/library/assets/{asset_id}/text", headers=headers)
    assert read.status_code == 200, read.text
    assert read.json()["data"]["excerpt"] == "第一版正文 第二行"

    edited = client.put(
        f"{api}/library/assets/{asset_id}/text",
        json={"content": "第二版正文", "expectedVersion": 1, "note": "改标题"},
        headers=headers,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["version"] == 2

    # 后保存的人用旧版本号：必须 409，不能静默覆盖
    stale = client.put(
        f"{api}/library/assets/{asset_id}/text",
        json={"content": "第三人写的", "expectedVersion": 1},
        headers=headers,
    )
    assert stale.status_code == 409, stale.text

    # 内容没变不制造空版本
    same = client.put(
        f"{api}/library/assets/{asset_id}/text",
        json={"content": "第二版正文", "expectedVersion": 2},
        headers=headers,
    )
    assert same.status_code == 200, same.text
    assert same.json()["data"]["version"] == 2

    versions = client.get(f"{api}/library/assets/{asset_id}/text/versions", headers=headers)
    assert versions.status_code == 200, versions.text
    history = versions.json()["data"]
    assert [item["version"] for item in history] == [2, 1]
    assert history[0]["note"] == "改标题"
    assert all(item["content"] is None for item in history), "列表不该带 1MB 正文"

    first = client.get(f"{api}/library/assets/{asset_id}/text/versions/1", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["data"]["content"] == "第一版正文\n第二行"

    restored = client.post(
        f"{api}/library/assets/{asset_id}/text/restore",
        json={"version": 1, "expectedVersion": 2},
        headers=headers,
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["version"] == 3
    assert restored.json()["data"]["content"] == "第一版正文\n第二行"

    # 恢复是「写成新的一版」：第 1 版仍原样，历史行不可变
    assert (
        client.get(f"{api}/library/assets/{asset_id}/text/versions/1", headers=headers)
        .json()["data"]["content"]
        == "第一版正文\n第二行"
    )
    assert [item["version"] for item in client.get(
        f"{api}/library/assets/{asset_id}/text/versions", headers=headers
    ).json()["data"]] == [3, 2, 1]


def test_list_item_exposes_text_fields_but_no_cover(client: TestClient, api: str) -> None:
    headers = _headers(client)
    created = _create(client, headers, f"摘要内容{suffix()}")

    page = client.get(
        f"{api}/library/assets", params={"pageSize": 100}, headers=headers
    ).json()["data"]
    item = next(row for row in page["items"] if row["id"] == created["id"])

    assert item["kind"] == "text"
    assert item["textExcerpt"].startswith("摘要内容")
    assert item["textVersion"] == 1
    assert item["textUrl"].endswith(f"/library/assets/{created['id']}/text/raw")
    assert item["coverUrl"] is None, "文本没有缩略图，前端靠 textExcerpt 画文字卡"

    selection = client.get(
        f"{api}/library/selection", params={"kind": "text", "pageSize": 100}, headers=headers
    )
    assert selection.status_code == 200, selection.text
    assert any(row["id"] == created["id"] for row in selection.json()["data"]["items"])

    images = client.get(
        f"{api}/library/selection", params={"kind": "image", "pageSize": 100}, headers=headers
    ).json()["data"]["items"]
    assert all(row["id"] != created["id"] for row in images)


# --------------------------------------------------------------------------- 上传 / 取件


def test_upload_normalizes_bom_and_serves_as_plain_text(client: TestClient, api: str) -> None:
    headers = _headers(client)
    body = f"# 标题{suffix()}\n正文".encode()
    response = _upload(client, headers, b"\xef\xbb\xbf" + body, filename="script.md")
    assert response.status_code == 201, response.text
    asset = response.json()["data"]

    assert asset["kind"] == "text"
    assert asset["sizeBytes"] == len(body), "BOM 必须被剥掉后再算内容寻址与大小"
    assert asset["coverUrl"] is None

    raw = client.get(f"{api}/library/assets/{asset['id']}/text/raw", headers=headers)
    assert raw.status_code == 200, raw.text
    assert raw.headers["content-type"].startswith("text/plain")
    assert raw.headers["x-content-type-options"] == "nosniff"
    assert raw.content == body
    assert not raw.content.startswith(b"\xef\xbb\xbf")

    text = client.get(f"{api}/library/assets/{asset['id']}/text", headers=headers)
    assert text.json()["data"]["content"] == body.decode("utf-8")
    assert text.json()["data"]["version"] == 1

    # 上传进来的文本同样能编辑，版本表不是只有站内新建才有
    edited = client.put(
        f"{api}/library/assets/{asset['id']}/text",
        json={"content": "改写后的正文", "expectedVersion": 1},
        headers=headers,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["version"] == 2


def test_txt_upload_is_accepted(client: TestClient, api: str) -> None:
    headers = _headers(client)
    response = _upload(
        client, headers, f"纯文本{suffix()}".encode(), filename="notes.txt"
    )
    assert response.status_code == 201, response.text
    assert response.json()["data"]["name"].endswith(".txt")


def test_upload_rejects_wrong_extension_encoding_size_and_kind(client: TestClient) -> None:
    headers = _headers(client)
    marker = suffix()

    assert _upload(client, headers, b"<b>x</b>", filename="page.html").status_code == 400
    assert _upload(client, headers, "中文".encode("gbk"), filename="gbk.md").status_code == 400
    assert _upload(client, headers, b" \n ", filename="empty.md").status_code == 400
    assert _upload(
        client, headers, b"x" * (1024 * 1024 + 1), filename="huge.md"
    ).status_code == 413
    # 后缀说文本、内容却不是 UTF-8：不能靠改扩展名绕过
    assert _upload(client, headers, png_bytes(), filename="fake.md").status_code == 400
    # 后缀是 .md 却按 image 上传：类型声明与字节不符
    assert _upload(
        client, headers, f"{marker}".encode(), filename="fake.md", kind="image"
    ).status_code == 400


def test_text_endpoints_reject_non_text_assets(client: TestClient, api: str) -> None:
    headers = _headers(client)
    response = _upload(client, headers, png_bytes(), filename="cover.png", kind="image")
    assert response.status_code == 201, response.text
    asset_id = response.json()["data"]["id"]

    assert client.get(f"{api}/library/assets/{asset_id}/text", headers=headers).status_code == 400
    assert (
        client.put(
            f"{api}/library/assets/{asset_id}/text",
            json={"content": "不该写进去"},
            headers=headers,
        ).status_code
        == 400
    )
    assert (
        client.get(f"{api}/library/assets/{asset_id}/text/raw", headers=headers).status_code
        == 400
    )


# --------------------------------------------------------------------------- 查重 / NUL


def test_create_same_content_twice_returns_existing_instead_of_conflict(
    client: TestClient,
) -> None:
    """站内新建同一段正文两次：**返回已有那条**，不新建第二份。

    唯一索引 `uq_library_assets_owner_sha_kind` 拦的是「同 owner + 同内容 +
    同类型」。站内新建当时没先查重，于是撞索引只抛通用 409
    「请检查唯一性或关联引用」——用户没做错任何事，只是把同一段文字又存了一遍。
    """
    headers = _headers(client)
    content = f"重复正文{suffix()}"
    first = _create(client, headers, content)
    second = _create(client, headers, content)

    # 秒传口径：同一条资产，不是两条
    assert second["id"] == first["id"]
    assert second["sha256"] == first["sha256"]

    # 库里确实只有一条
    listing = client.get(
        f"{API}/library/assets",
        params={"view": "personal", "kind": "text"},
        headers=headers,
    )
    assert listing.status_code == 200, listing.text
    ids = [item["id"] for item in listing.json()["data"]["items"]]
    assert ids.count(first["id"]) == 1
    assert first["id"] in ids


def test_update_to_another_asset_content_reports_duplicate_instead_of_409(
    client: TestClient,
) -> None:
    """编辑成「自己名下另一条一模一样的正文」要指名道姓，不能只丢一个 409。"""
    headers = _headers(client)
    marker = suffix()
    first = _create(client, headers, f"甲{marker}")
    second = _create(client, headers, f"乙{marker}")

    response = client.put(
        f"{API}/library/assets/{second['id']}/text",
        json={"content": f"甲{marker}", "expectedVersion": second["version"]},
        headers=headers,
    )
    assert response.status_code == 409, response.text
    body = response.json()
    # 文案要指向那一条同名素材，用户才知道该删哪份
    assert first["name"] in body["message"]
    assert body["details"]["duplicateId"] == first["id"]


def test_text_containing_nul_is_rejected_without_leaving_orphan_bytes(
    client: TestClient,
) -> None:
    """U+0000 是合法 UTF-8，但 PostgreSQL 的 text 列拒存。

    上传链是「先 put blob、再落库」，不在落盘前拦就会留下**没人引用的字节**，
    而库里那条记录根本没建成，用户只看到一个 500。
    """
    headers = _headers(client)
    marker = suffix()

    created = client.post(
        f"{API}/library/text-assets",
        json={"name": f"带空字符{suffix()}", "content": f"前{marker}\x00后", "extension": "md"},
        headers=headers,
    )
    assert created.status_code == 400, created.text
    assert "U+0000" in created.json()["message"]

    # 上传链同样要拦
    uploaded = _upload(client, headers, f"前{marker}\x00后".encode(), filename="nul.md")
    assert uploaded.status_code == 400, uploaded.text
    assert "U+0000" in uploaded.json()["message"]
