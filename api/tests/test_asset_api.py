"""素材接口：上传、去重、分页、删除与引用拦截。

有意覆盖的失败路径比成功路径多——上传的每条校验（类型、大小、内容伪装）都是
「不写在这儿就等于没有」的那种代码，成功路径反而是最容易碰巧通过的。

**合并之后这份文件变短了**：`/assets` 现在就是归档那一套（`/assets/archive` 已
删除），旧的 `coverUrl` / `fileUrl` / `counts` 三个出参随旧实现一起消失，对应的
断言也不再需要。留下的是与「接口形状」无关的守卫：类型与内容校验、去重、分页
不重不漏、Range、引用拦截、未知 id。
"""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from core.ports.assets import register_asset_reference_checker, reset_asset_reference_checker

PngUploader = Callable[[str, bytes], dict[str, Any]]


def png_bytes(size: tuple[int, int] = (40, 60), color: str = "purple") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _upload(
    client: TestClient, api: str, filename: str, content: bytes, category: str = "character"
):
    return client.post(
        f"{api}/assets/upload",
        content=content,
        params={"filename": filename, "category": category},
    )


def _trash(client: TestClient, api: str, asset: dict[str, Any]):
    return client.put(
        f"{api}/assets/{asset['id']}/deleted",
        json={"expectedVersion": asset["stateVersion"], "deleted": True},
    )


@pytest.fixture
def create(client: TestClient, api: str) -> PngUploader:
    """造一条素材并登记回收，测试结束时逐条删掉。

    不走直接改库：这里共享的是同一套会话与事件循环，另开一个循环去动
    引擎会有 loop 绑定的隐患，而「用接口造数、用接口清理」本身就是
    对删除路径的顺带验证。
    """

    def make(filename: str, content: bytes) -> dict[str, Any]:
        response = _upload(client, api, filename, content)
        assert response.status_code == 201, response.text
        return response.json()["data"]

    yield make

    # 收尾要按「先进回收站 → 再彻底删」两步走：合并后单条 DELETE 只接受回收站
    # 里的记录，直接删会 400，测试收尾就会互相留下垃圾
    listed = client.get(f"{api}/assets", params={"scope": "mine", "pageSize": 200})
    for item in listed.json()["data"]["items"]:
        if not item["name"].startswith(("up-", "dup-", "page-", "serve-", "range-", "used-")):
            continue
        if item["deletedAt"] is None:
            client.put(
                f"{api}/assets/{item['id']}/deleted",
                json={"expectedVersion": item["stateVersion"], "deleted": True},
            )
        client.delete(f"{api}/assets/{item['id']}")


def test_upload_returns_metadata_and_derived_cover(create: PngUploader, api: str) -> None:
    data = create("up-basic.png", png_bytes((40, 60)))

    assert data["name"] == "up-basic.png"
    assert data["type"] == "image"
    assert data["mimeType"] == "image/png"
    assert (data["width"], data["height"]) == (40, 60)
    assert data["category"] == "character"
    # 取件地址指向本服务，不是存储驱动给的 `/static/<key>`：后者根本没挂载，
    # 前端拿它渲染就是一片裂图
    assert data["url"] == f"{api}/assets/{data['id']}/file"
    assert data["sha256"], "摘要既是去重键也是存储 key 的来源"
    assert data["stateVersion"] == 0


def test_duplicate_upload_reuses_the_record(client: TestClient, api: str) -> None:
    """同一账号重复上传同一文件：返回同一条记录，不产生第二份。"""
    payload = png_bytes((32, 32), "teal")
    first = _upload(client, api, "dup-a.png", payload).json()["data"]
    second = _upload(client, api, "dup-b.png", payload).json()["data"]

    assert first["id"] == second["id"], "同内容重复上传应复用旧记录"
    ids = {
        item["id"]
        for item in client.get(f"{api}/assets", params={"keyword": "dup-"}).json()["data"]["items"]
    }
    assert ids == {first["id"]}


def test_upload_rejects_unknown_extension(client: TestClient, api: str) -> None:
    response = _upload(client, api, "evil.exe", b"MZ\x90\x00")

    assert response.status_code == 400
    assert response.json()["code"] == 40051


def test_upload_rejects_content_that_lies_about_its_type(
    client: TestClient, api: str
) -> None:
    """扩展名写 .png、内容不是图片：必须拒绝，不能入库一个打不开的"图片"。"""
    assert _upload(client, api, "fake.png", b"not an image at all").status_code == 400


def test_upload_rejects_empty_body(client: TestClient, api: str) -> None:
    assert _upload(client, api, "empty.png", b"").status_code == 400


def test_upload_enforces_size_limit(
    client: TestClient, api: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上限用常量控制，测试里调小，不真的造 300MB 流量。"""
    from modules.asset import archive_service

    monkeypatch.setattr(archive_service, "MAX_BYTES", 1024)

    response = _upload(client, api, "big.png", png_bytes((80, 80)) + b"\x00" * 4096)

    assert response.status_code == 400
    assert "300MB" in response.text


def test_list_paginates_without_gaps_or_duplicates(
    client: TestClient, api: str, create: PngUploader
) -> None:
    for index in range(3):
        create(f"page-{index}.png", png_bytes((10 + index, 10), "red"))

    first = client.get(
        f"{api}/assets", params={"keyword": "page-", "page": 1, "pageSize": 2}
    ).json()["data"]
    second = client.get(
        f"{api}/assets", params={"keyword": "page-", "page": 2, "pageSize": 2}
    ).json()["data"]

    assert first["total"] == 3
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    ids = [item["id"] for item in first["items"] + second["items"]]
    assert len(set(ids)) == 3, "翻页不能重复也不能漏行"
    assert first["items"][0]["createdAt"] >= first["items"][1]["createdAt"], "倒序"


def test_delete_is_blocked_when_referenced(
    client: TestClient, api: str, create: PngUploader
) -> None:
    """注入口契约：被引用的素材必须 409 并说明被谁引用。

    拦截点在「移入回收站」这一步，不在彻底删除那一步：等它进了回收站才发现被引用，
    用户已经看不到它了，却还得再翻回来恢复。

    真实实现（`template_pack_references.asset_id`）的端到端覆盖见
    `test_asset_reference_sources.py`；任务的参考图已切到 v2，见
    `test_task_library_references.py`。这里用替身，是为了单独验证
    「服务层确实问了注入口」且描述原样透出。
    """
    from modules.template.asset_references import PackAssetReferences

    asset = create("used.png", png_bytes((20, 20), "blue"))

    class FakeChecker:
        async def references(self, asset_ids):
            return {asset_id: ["模板《圣诞活动》"] for asset_id in asset_ids}

    # 模块 import 期已经注册了真实实现，先摘下来才能插替身
    reset_asset_reference_checker()
    register_asset_reference_checker(FakeChecker())
    try:
        response = _trash(client, api, asset)
    finally:
        reset_asset_reference_checker()
        register_asset_reference_checker(PackAssetReferences())

    assert response.status_code == 409
    assert response.json()["code"] == 40951
    assert "圣诞活动" in str(response.json()["details"])


def test_purging_requires_the_trash_first(
    client: TestClient, api: str, create: PngUploader
) -> None:
    """彻底删除只接受回收站里的记录：一步到位等于没有二次确认。"""
    asset = create("used-purge.png", png_bytes((12, 12), "navy"))

    direct = client.delete(f"{api}/assets/{asset['id']}")
    assert direct.status_code == 400
    assert "回收站" in direct.text

    assert _trash(client, api, asset).status_code == 200
    assert client.delete(f"{api}/assets/{asset['id']}").status_code == 204


def test_file_endpoints_serve_bytes(client: TestClient, api: str, create: PngUploader) -> None:
    asset = create("serve.png", png_bytes((24, 24), "green"))

    file_response = client.get(f"{api}/assets/{asset['id']}/file")
    thumbnail_response = client.get(f"{api}/assets/{asset['id']}/thumbnail")

    assert file_response.status_code == 200
    assert file_response.headers["content-type"] == "image/png"
    assert file_response.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert thumbnail_response.status_code == 200


def test_file_endpoint_supports_range_requests(
    client: TestClient, api: str, create: PngUploader
) -> None:
    """视频拖动进度条依赖 Range：本地驱动必须走 FileResponse 而不是整包返回。"""
    asset = create("range.png", png_bytes((30, 30), "white"))

    response = client.get(f"{api}/assets/{asset['id']}/file", headers={"Range": "bytes=0-15"})

    assert response.status_code == 206
    assert len(response.content) == 16


def test_unknown_asset_is_404(client: TestClient, api: str) -> None:
    assert client.get(f"{api}/assets/{'0' * 32}").status_code == 404
    assert client.delete(f"{api}/assets/{'0' * 32}").status_code == 404
    assert client.get(f"{api}/assets/{'0' * 32}/file").status_code == 404
