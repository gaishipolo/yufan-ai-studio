"""跨账号原片分享、专员发布与输入快照，不访问真实盘或模型。

素材一律走 HTTP 上传登记：索引在 `assets` 表，服务层构造需要 session，
测试里直接调同步仓储是「JSON 即索引」时代的写法，留着会掩盖真实的入库路径。
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import imageio_ffmpeg
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from api.deps import get_actor
from core.context import CurrentUser
from core.exceptions import BadRequest, NotFound
from infra.archive import checksum
from infra.archive.local import get_archive_settings
from infra.media_catalog import MediaCatalog
from modules.template.pack_service import PackService
from modules.template.replacement_schema import PackDraftIn, PrepareReplacement
from modules.template.replacement_service import prepare

API = "/api/v1"
PERMISSIONS = ("asset:read", "asset:create", "asset:share", "asset:update", "asset:delete")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient):
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    yield tmp_path
    client.app.dependency_overrides.pop(get_actor, None)


def actor(client: TestClient, owner: str, *permissions: str) -> None:
    client.app.dependency_overrides[get_actor] = lambda: CurrentUser(
        id=owner, name=owner, permissions=frozenset(permissions)
    )


def make_video(path: Path) -> Path:
    """2 秒 H.264 + AAC 短视频；颜色随机，避免同分类内被摘要去重。"""
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x{os.urandom(3).hex()}:s=64x64:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def source(client: TestClient, root: Path, owner: str = "A") -> dict:
    """以 `owner` 身份上传一份原片，返回与前端完全一致的出参。"""
    path = make_video(root / f"{uuid.uuid4().hex}.mp4")
    actor(client, owner, *PERMISSIONS)
    response = client.post(
        f"{API}/assets/archive/upload",
        params={"filename": "私有原片.mp4", "category": "source"},
        content=path.read_bytes(),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert isinstance(data, dict)
    return data


def change(
    client: TestClient, asset_id: str, owner: str, action: str, **fields: object
) -> object:
    """按当前 `stateVersion` 提交一次状态变更，返回响应（调用方自行断言状态码）。"""
    actor(client, owner, *PERMISSIONS)
    detail = client.get(f"{API}/assets/archive/{asset_id}")
    assert detail.status_code == 200, detail.text
    version = detail.json()["data"]["stateVersion"]
    return client.put(
        f"{API}/assets/archive/{asset_id}/{action}",
        json={"expectedVersion": version, **fields},
    )


def pack(client: TestClient, root: Path) -> tuple[str, int]:
    """封装一张参考图并发布，返回（包 id, 版本）。

    参考图也走 HTTP 上传：专员维度的 `asset:reference:manage` 是这条路径的准入条件，
    直接写 JSON 会绕开它。
    """
    path = root / "reference.png"
    # 颜色随机：归档按 (owner, sha256, category) 去重，而 `assets` 表在测试间共享，
    # 写死内容会让第二个用例命中上一个用例已被清掉的 tmp_path 记录。
    Image.new("RGB", (16, 16), tuple(os.urandom(3))).save(path, format="PNG")
    actor(client, "S", "asset:read", "asset:create", "asset:reference:manage")
    response = client.post(
        f"{API}/assets/archive/upload",
        params={"filename": "产品.png", "category": "product"},
        content=path.read_bytes(),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 201, response.text
    image = response.json()["data"]
    service = PackService()
    draft = service.save(
        PackDraftIn(
            request_id=uuid.uuid4(),
            kind="product",
            name="专员产品",
            reference_ids=[image["id"]],
            cover_reference_id=image["id"],
            # 产品包发布必须声明用途与可替换范围，否则 `gaps` 会拦住发布。
            bindings=[{"role": "front"}],
            supported_scopes=["worn", "held"],
        ),
        "S",
    )
    published = service.publish(draft.id, "S", draft.revision)
    return published.id, published.version


def test_private_share_revoke_and_owner_management(workspace: Path, client: TestClient) -> None:
    item = source(client, workspace)
    actor(client, "B", "asset:read", "asset:share", "asset:update", "asset:delete")
    assert client.get(f"{API}/assets/archive?scope=public").json()["data"]["total"] == 0
    assert client.get(item["url"]).status_code == 404
    assert client.get(f"{API}/assets/archive/{item['id']}").status_code == 404
    assert (
        client.put(
            f"{API}/assets/archive/{item['id']}/sharing",
            json={"expectedVersion": 0, "shared": True},
        ).status_code
        == 404
    )
    assert change(client, item["id"], "A", "sharing", shared=True).status_code == 200
    actor(client, "B", "asset:read")
    data = client.get(f"{API}/assets/archive?scope=public").json()["data"]
    assert data["total"] == 1 and data["root"] == ""
    assert data["items"][0]["path"] == "" and not data["items"][0]["downloadAllowed"]
    assert client.get(item["url"]).status_code == 404  # 分享不打开原件下载端点
    # 公共预览是唯一放行的字节出口
    assert client.get(f"{API}/assets/archive/{item['id']}/preview").status_code == 200
    assert change(client, item["id"], "A", "sharing", shared=False).status_code == 200
    actor(client, "B", "asset:read")
    assert client.get(f"{API}/assets/archive/{item['id']}").status_code == 404


def test_recycle_restore_private_and_rename_preserves_bytes(
    workspace: Path, client: TestClient
) -> None:
    item = source(client, workspace)
    assert change(client, item["id"], "A", "name", name="新名称").status_code == 200
    assert change(client, item["id"], "A", "sharing", shared=True).status_code == 200
    assert change(client, item["id"], "A", "deleted", deleted=True).status_code == 200
    actor(client, "B", "asset:read")
    assert client.get(f"{API}/assets/archive?scope=public").json()["data"]["total"] == 0
    actor(client, "A", *PERMISSIONS)
    trash = client.get(f"{API}/assets/archive", params={"scope": "trash"}).json()["data"]
    assert trash["total"] == 1
    assert change(client, item["id"], "A", "deleted", deleted=False).status_code == 200
    restored = client.get(f"{API}/assets/archive/{item['id']}").json()["data"]
    assert not restored["shared"] and restored["name"] == "新名称"
    assert checksum(workspace / item["path"]) == item["sha256"]
    # 拿旧版本号重放：状态在三次变更后已推进，必须按冲突拒绝而不是静默成功
    stale = client.put(
        f"{API}/assets/archive/{item['id']}/sharing",
        json={"expectedVersion": 0, "shared": True},
    )
    assert stale.status_code == 409


def test_editor_cannot_upload_references_or_bypass_share_fields(
    workspace: Path, client: TestClient
) -> None:
    actor(client, "A", "asset:create", "asset:read", "asset:share")
    response = client.post(
        f"{API}/assets/archive/upload?category=product&filename=x.png", content=b"x"
    )
    assert response.status_code == 403
    assert (
        client.post(
            f"{API}/assets/archive/import", json={"relativePath": "x", "category": "source"}
        ).status_code
        == 403
    )
    item = source(client, workspace)
    assert (
        client.put(
            f"{API}/assets/archive/{item['id']}/sharing",
            json={"expectedVersion": 0, "shared": True, "deleted": True},
        ).status_code
        == 400
    )


def test_specialist_publication_and_preview_are_cross_account(
    workspace: Path, client: TestClient
) -> None:
    uid, version = pack(client, workspace)
    actor(client, "B", "asset:read", "template:read", "template:use")
    result = client.get(f"{API}/templates/replacement-packs/available")
    assert result.status_code == 200
    published = result.json()["data"]["items"][0]
    assert published["id"] == uid and published["available"]
    assert client.get(published["coverUrl"]).status_code == 200
    assert client.get(f"{API}/templates/replacement-packs").status_code == 403
    assert (
        client.post(
            f"{API}/templates/replacement-packs/{uid}/disable", json={"expectedRevision": 1}
        ).status_code
        == 403
    )
    assert version == 1


def test_shared_source_and_published_target_create_callers_snapshot(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    from modules.template import replacement_service

    monkeypatch.setattr(
        replacement_service,
        "_compile",
        lambda *_: replacement_service.Compilation(
            prompt="isolated compiled prompt", compiler_version="test"
        ),
    )
    item = source(client, workspace)
    uid, version = pack(client, workspace)
    payload = PrepareReplacement(
        request_id=uuid.uuid4(),
        kind="product",
        source_id=item["id"],
        pack_id=uid,
        pack_version=version,
        reference_ids=["forged"],
        target="forged",
        # 产品包只接受它审核过的范围；留空会被 `_check_scope` 直接拒绝。
        replacement_scope="人物上身穿着",
    )
    with pytest.raises(NotFound):
        prepare(payload, "B", require_pack=True)
    assert change(client, item["id"], "A", "sharing", shared=True).status_code == 200
    run = prepare(payload, "B", require_pack=True)
    assert run.target == "专员产品"
    path = Path(run.archive_path)
    assert (path / "inputs/source-original.mp4").read_bytes() == (
        workspace / item["path"]
    ).read_bytes()
    assert MediaCatalog().storage.read(path / "run.json")["owner_id"] == "B"
    MediaCatalog().check_run(path)
    assert change(client, item["id"], "A", "sharing", shared=False).status_code == 200
    with pytest.raises(NotFound):
        MediaCatalog().check_run(path)
    assert (path / "inputs/source-original.mp4").is_file()
    from infra.ai import libtv_process

    def no_upstream(*args, **kwargs):
        pytest.fail("撤回共享后不能启动上游提交进程")

    monkeypatch.setattr(libtv_process.subprocess, "Popen", no_upstream)
    result = libtv_process.supervise("submit", path, workspace / "isolated-legacy", "node")
    assert result.code == "AUTHORIZATION_REVOKED"


def test_no_pack_http_rejected_and_readonly_cannot_prepare(
    workspace: Path, client: TestClient
) -> None:
    item = source(client, workspace, "B")
    payload = {
        "requestId": str(uuid.uuid4()),
        "kind": "product",
        "sourceId": item["id"],
        "referenceIds": ["arbitrary"],
        "target": "x",
    }
    actor(client, "B", "asset:read", "template:use")
    assert client.post(f"{API}/templates/replacement-runs", json=payload).status_code == 400
    actor(client, "B", "asset:read", "template:read")
    assert client.post(f"{API}/templates/replacement-runs", json=payload).status_code == 403


def test_publish_idempotent_version_and_disable(workspace: Path, client: TestClient) -> None:
    uid, _ = pack(client, workspace)
    service = PackService()
    assert service.publish(uid, "S", 1).version == 1
    old = service.catalog.pack(uid)
    draft = service.save(
        PackDraftIn(
            request_id=uuid.UUID(uid),
            expected_revision=1,
            kind="product",
            name="第二版",
            reference_ids=old.reference_ids,
            cover_reference_id=old.cover_reference_id,
            bindings=old.bindings,
            supported_scopes=old.supported_scopes,
        ),
        "S",
    )
    assert service.publish(uid, "S", draft.revision).version == 2
    assert service.catalog.published(uid, 1).name == "专员产品"
    service.disable(uid, "S", draft.revision)
    with pytest.raises(BadRequest):
        service.catalog.published(uid, 1)


def test_legacy_pack_is_not_automatically_public(workspace: Path, client: TestClient) -> None:
    uid, _ = pack(client, workspace)
    catalog = MediaCatalog()
    head = catalog.pack(uid)
    data = head.model_dump(mode="json", by_alias=True)
    for key in ("status", "version", "publishedAt"):
        data.pop(key, None)
    catalog.storage.write(catalog.storage.resolve(f"catalog/packs/{uid}.json"), data)
    assert not PackService().available()
    assert PackService().drafts("S").items


def test_archive_list_rejects_unknown_filter(
    client: TestClient, workspace: Path
) -> None:
    """范围与状态进契约：非法值由框架回 422，而不是落到业务分支里 400。"""
    actor(client, "u_filter", "asset:read")
    assert client.get(f"{API}/assets/archive", params={"scope": "everyone"}).status_code == 422
    assert client.get(f"{API}/assets/archive", params={"state": "deleted"}).status_code == 422
    assert client.get(f"{API}/assets/archive", params={"category": "video"}).status_code == 422
