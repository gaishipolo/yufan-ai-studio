"""案例用途（`purpose=case`）的边界。

「案例」是对**所有登录用户**可见的内容：模板卡片上的封面、悬停播放的那段
视频，都是这条素材本身。所以它不能有「个人素材」那种宽松待遇：

- **媒体类型**：音频没有封面、文本没有画幅，卡片上什么也显示不出来；
  而 `purpose=case` 对所有人放行，一条音频案例就是一条人人可读却看不出来的
  东西。两条入口（上传带 purpose / 改用途）都必须过这道闸。
- **删除**：撤下案例影响的是所有人的模板卡片，不能像个人素材那样一键进
  回收站、无理由、无凭据。
- **恢复**：恢复不恢复「对全员可见」的身份 —— 与 `visibility` 强制降回
  `personal`（绝不恢复共享）是同一个口径，要重新走一次 `set_purpose`。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from library_support import (
    LIB_RW,
    admin_headers,
    fetch_asset,
    insert_asset,
    make_user,
    png_bytes,
)


def _upload_case(
    client: TestClient, api: str, headers: dict[str, str], *, filename: str, kind: str, body: bytes
) -> dict[str, object]:
    response = client.post(
        f"{api}/library/assets/upload",
        content=body,
        params={"filename": filename, "kind": kind, "purpose": "case"},
        headers=headers,
    )
    return {"status": response.status_code, "body": response.text}


def test_upload_as_case_rejects_audio(client: TestClient, api: str) -> None:
    """音频当案例：既没有封面，也没有画幅，卡片上呈现不出来。"""
    headers = admin_headers(client)
    result = _upload_case(
        client,
        api,
        headers,
        filename="voice.mp3",
        kind="audio",
        body=b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 64,
    )
    assert result["status"] == 400, result["body"]
    assert "案例只支持图片或视频" in str(result["body"])


def test_upload_as_case_rejects_text(client: TestClient, api: str) -> None:
    """文本同理：案例要能被「看见」，纯文本没有封面。"""
    headers = admin_headers(client)
    result = _upload_case(
        client,
        api,
        headers,
        filename="prompt.txt",
        kind="text",
        body="一段提示词".encode(),
    )
    assert result["status"] == 400, result["body"]
    assert "案例只支持图片或视频" in str(result["body"])


def test_upload_as_case_accepts_image(client: TestClient, api: str) -> None:
    """图片案例是正常路径：落库时就是案例，且不进个人列表。"""
    headers = admin_headers(client)
    response = client.post(
        f"{api}/library/assets/upload",
        content=png_bytes("teal", (33, 31)),
        params={"filename": "case-cover.png", "kind": "image", "purpose": "case"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["purpose"] == "case"

    listed = client.get(f"{api}/library/assets", headers=headers).json()["data"]
    assert all(item["purpose"] != "case" for item in listed["items"])


def test_deleting_a_case_requires_a_reason(client: TestClient, api: str) -> None:
    """案例撤下影响所有人的卡片，不能像个人素材那样一键无理由地删。"""
    admin = admin_headers(client)
    asset_id = insert_asset(
        owner_id="u_001",
        name="案例封面.png",
        kind="image",
        state="active",
        visibility="personal",
        purpose="case",
    )

    denied = client.delete(f"{api}/library/assets/{asset_id}", headers=admin)
    assert denied.status_code == 400, denied.text
    assert "理由" in denied.text

    allowed = client.delete(
        f"{api}/library/assets/{asset_id}?reason=换了新封面", headers=admin
    )
    assert allowed.status_code == 200, allowed.text
    assert fetch_asset(asset_id)["state"] == "trash"


def test_restore_drops_the_case_purpose(client: TestClient, api: str) -> None:
    """恢复不恢复「对全员可见」：与共享一样，要重新走一次申请。"""
    admin = admin_headers(client)
    asset_id = insert_asset(
        owner_id="u_001",
        name="案例封面.png",
        kind="image",
        state="active",
        visibility="personal",
        purpose="case",
    )

    deleted = client.delete(
        f"{api}/library/assets/{asset_id}?reason=撤下", headers=admin
    )
    assert deleted.status_code == 200, deleted.text

    restored = client.post(f"{api}/library/trash/{asset_id}/restore", headers=admin)
    assert restored.status_code == 200, restored.text

    row = fetch_asset(asset_id)
    assert row is not None
    assert row["purpose"] == ""
    assert row["visibility"] == "personal"
    assert row["state"] == "active"


def test_a_case_stays_visible_to_other_accounts(client: TestClient, api: str) -> None:
    """两条口径要分开：读某条案例素材 ≠ 浏览案例池。

    模板卡片要对所有登录用户展示案例封面，所以没有 `library:case:read`
    也能读这条素材的详情/字节；但「案例池」的列表入口仍然要那个权限。
    """
    admin = admin_headers(client)
    _user, headers = make_user(client, admin, LIB_RW)

    uploaded = client.post(
        f"{api}/library/assets/upload",
        content=png_bytes("teal", (33, 31)),
        params={"filename": "case-cover.png", "kind": "image", "purpose": "case"},
        headers=admin,
    )
    assert uploaded.status_code == 201, uploaded.text
    asset_id = uploaded.json()["data"]["id"]

    visible = client.get(f"{api}/library/assets/{asset_id}", headers=headers)
    assert visible.status_code == 200, visible.text
    assert client.get(f"{api}/library/assets/{asset_id}/file", headers=headers).status_code == 200
    assert (
        client.get(f"{api}/library/assets/{asset_id}/thumbnail", headers=headers).status_code
        == 200
    )

    # 但案例池仍然是「浏览入口」，不是登录即可翻。
    denied = client.get(
        f"{api}/library/assets", params={"purpose": "case"}, headers=headers
    )
    assert denied.status_code == 403, denied.text


def test_selection_can_look_at_the_case_pool(client: TestClient, api: str) -> None:
    """模板封装页的「从资产库选图片/视频」要能挑到已上传的案例。

    案例不进普通列表、选择器默认也把它排掉 —— 少了这一档，作者传过的案例
    第二次就用不上了（只能用一次），而它本来就是给模板卡片复用的。
    """
    admin = admin_headers(client)
    case_id = insert_asset(
        owner_id="u_001",
        name="案例封面.png",
        kind="image",
        state="active",
        visibility="personal",
        purpose="case",
    )
    plain_id = insert_asset(
        owner_id="u_001",
        name="普通图.png",
        kind="image",
        state="active",
        visibility="personal",
        purpose="",
    )

    def ids(**params: str) -> list[str]:
        response = client.get(
            f"{api}/library/selection", params={"kind": "image", **params}, headers=admin
        )
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()["data"]["items"]]

    # 默认口径不变：普通素材在，案例不在（与资产库列表同一条规则）
    assert plain_id in ids()
    assert case_id not in ids()
    # 案例池：只看得到案例（同库里可能还有别的用例留下的案例，不按「只有一条」断）
    pool = ids(purpose="case")
    assert case_id in pool
    assert plain_id not in pool


def test_selection_rejects_an_unknown_purpose(client: TestClient, api: str) -> None:
    """写错的用途要当场报错 —— 静默忽略会让人以为自己在挑案例。"""
    admin = admin_headers(client)
    response = client.get(
        f"{api}/library/selection", params={"purpose": "demo"}, headers=admin
    )
    assert response.status_code == 400, response.text
    assert "不支持的用途" in response.text


def test_selection_needs_case_read_for_the_case_pool(client: TestClient, api: str) -> None:
    """看案例池要 `library:case:read`：那是全员可见的内容，不是「谁都能翻」。"""
    admin = admin_headers(client)
    _user, headers = make_user(client, admin, LIB_RW)
    denied = client.get(
        f"{api}/library/selection", params={"purpose": "case"}, headers=headers
    )
    assert denied.status_code == 403, denied.text


def test_case_pool_list_needs_case_read(client: TestClient, api: str) -> None:
    """资产库列表的案例池入口同样要 `library:case:read`（与选择器同一道闸）。"""
    admin = admin_headers(client)
    _user, headers = make_user(client, admin, LIB_RW)
    denied = client.get(
        f"{api}/library/assets", params={"purpose": "case"}, headers=headers
    )
    assert denied.status_code == 403, denied.text
