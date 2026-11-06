"""跨账号原片分享、专员发布与输入快照，不访问真实盘或模型。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from api.deps import get_actor
from core.context import CurrentUser
from core.exceptions import BadRequest, Conflict, NotFound
from core.media import StoredMedia
from infra.archive import checksum
from infra.archive.local import get_archive_settings
from infra.media_catalog import MediaCatalog
from modules.asset.archive_repository import ArchiveAssetRepository
from modules.asset.archive_schema import ArchiveChange
from modules.asset.archive_service import ArchiveAssetService
from modules.template.pack_service import PackService
from modules.template.replacement_schema import PackDraftIn, PrepareReplacement
from modules.template.replacement_service import prepare


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient):
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    yield tmp_path
    client.app.dependency_overrides.pop(get_actor, None)


def actor(client: TestClient, owner: str, *permissions: str) -> None:
    client.app.dependency_overrides[get_actor] = lambda: CurrentUser(
        id=owner, name=owner, permissions=frozenset(permissions)
    )


def source(root: Path, owner: str = "A") -> StoredMedia:
    uid = uuid.uuid4().hex
    path = root / f"{uid}.mp4"
    path.write_bytes(b"isolated-original-video")
    item = StoredMedia(
        id=uid,
        owner_id=owner,
        name="私有原片.mp4",
        category="source",
        type="video",
        mime_type="video/mp4",
        size_bytes=path.stat().st_size,
        path=path.name,
        sha256=checksum(path),
        created_at=datetime.now(UTC),
        duration=2,
        has_audio=True,
        url=f"/api/v1/assets/archive/{uid}/file",
    )
    ArchiveAssetRepository().save(item)
    return item


def pack(root: Path) -> tuple[str, int]:
    path = root / "reference.png"
    Image.new("RGB", (16, 16), "blue").save(path)
    image = ArchiveAssetRepository().register(path, "产品.png", "product", "S")
    service = PackService()
    draft = service.save(
        PackDraftIn(
            request_id=uuid.uuid4(),
            kind="product",
            name="专员产品",
            reference_ids=[image.id],
            cover_reference_id=image.id,
            # 产品包发布必须声明用途与可替换范围，否则 `gaps` 会拦住发布。
            bindings=[{"role": "front"}],
            supported_scopes=["worn", "held"],
        ),
        "S",
    )
    published = service.publish(draft.id, "S", draft.revision)
    return published.id, published.version


def test_private_share_revoke_and_owner_management(workspace: Path, client: TestClient) -> None:
    item = source(workspace)
    actor(client, "B", "asset:read", "asset:share", "asset:update", "asset:delete")
    assert client.get("/api/v1/assets/archive?scope=public").json()["data"]["total"] == 0
    assert client.get(item.url).status_code == 404
    assert client.get(f"/api/v1/assets/archive/{item.id}").status_code == 404
    assert (
        client.put(
            f"/api/v1/assets/archive/{item.id}/sharing", json={"expectedVersion": 0, "shared": True}
        ).status_code
        == 404
    )
    actor(client, "A", "asset:read", "asset:share", "asset:update", "asset:delete")
    result = client.put(
        f"/api/v1/assets/archive/{item.id}/sharing", json={"expectedVersion": 0, "shared": True}
    )
    assert result.status_code == 200
    actor(client, "B", "asset:read")
    data = client.get("/api/v1/assets/archive?scope=public").json()["data"]
    assert data["total"] == 1 and data["root"] == ""
    assert data["items"][0]["path"] == "" and not data["items"][0]["downloadAllowed"]
    assert client.get(item.url).status_code == 404  # 分享不打开原件下载端点
    ArchiveAssetService().change(item.id, "A", ArchiveChange(expected_version=1, shared=False))
    assert client.get(f"/api/v1/assets/archive/{item.id}").status_code == 404


def test_recycle_restore_private_and_rename_preserves_bytes(workspace: Path) -> None:
    item = source(workspace)
    service = ArchiveAssetService()
    service.change(item.id, "A", ArchiveChange(expected_version=0, name="新名称"))
    service.change(item.id, "A", ArchiveChange(expected_version=1, shared=True))
    service.change(item.id, "A", ArchiveChange(expected_version=2, deleted=True))
    assert service.list_assets("B", "source", "public").total == 0
    assert service.list_assets("A", "source", "trash").total == 1
    service.change(item.id, "A", ArchiveChange(expected_version=3, deleted=False))
    restored = service.detail(item.id, "A")
    assert not restored.shared and restored.name == "新名称"
    assert checksum(workspace / item.path) == item.sha256
    with pytest.raises(Conflict):
        service.change(item.id, "A", ArchiveChange(expected_version=0, shared=True))


def test_editor_cannot_upload_references_or_bypass_share_fields(
    workspace: Path, client: TestClient
) -> None:
    actor(client, "A", "asset:create", "asset:read", "asset:share")
    response = client.post(
        "/api/v1/assets/archive/upload?category=product&filename=x.png", content=b"x"
    )
    assert response.status_code == 403
    assert (
        client.post(
            "/api/v1/assets/archive/import", json={"relativePath": "x", "category": "source"}
        ).status_code
        == 403
    )
    item = source(workspace)
    assert (
        client.put(
            f"/api/v1/assets/archive/{item.id}/sharing",
            json={"expectedVersion": 0, "shared": True, "deleted": True},
        ).status_code
        == 400
    )


def test_specialist_publication_and_preview_are_cross_account(
    workspace: Path, client: TestClient
) -> None:
    uid, version = pack(workspace)
    actor(client, "B", "asset:read", "template:read", "template:use")
    result = client.get("/api/v1/templates/replacement-packs/available")
    assert result.status_code == 200
    published = result.json()["data"]["items"][0]
    assert published["id"] == uid and published["available"]
    assert client.get(published["coverUrl"]).status_code == 200
    assert client.get("/api/v1/templates/replacement-packs").status_code == 403
    assert (
        client.post(
            f"/api/v1/templates/replacement-packs/{uid}/disable", json={"expectedRevision": 1}
        ).status_code
        == 403
    )
    assert version == 1


def test_shared_source_and_published_target_create_callers_snapshot(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from modules.template import replacement_service

    monkeypatch.setattr(
        replacement_service,
        "_compile",
        lambda *_: replacement_service.Compilation(
            prompt="isolated compiled prompt", compiler_version="test"
        ),
    )
    item = source(workspace)
    uid, version = pack(workspace)
    payload = PrepareReplacement(
        request_id=uuid.uuid4(),
        kind="product",
        source_id=item.id,
        pack_id=uid,
        pack_version=version,
        reference_ids=["forged"],
        target="forged",
        # 产品包只接受它审核过的范围；留空会被 `_check_scope` 直接拒绝。
        replacement_scope="人物上身穿着",
    )
    with pytest.raises(NotFound):
        prepare(payload, "B", require_pack=True)
    ArchiveAssetService().change(item.id, "A", ArchiveChange(expected_version=0, shared=True))
    run = prepare(payload, "B", require_pack=True)
    assert run.target == "专员产品"
    path = Path(run.archive_path)
    assert (path / "inputs/source-original.mp4").read_bytes() == (
        workspace / item.path
    ).read_bytes()
    assert MediaCatalog().storage.read(path / "run.json")["owner_id"] == "B"
    MediaCatalog().check_run(path)
    ArchiveAssetService().change(item.id, "A", ArchiveChange(expected_version=1, shared=False))
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
    item = source(workspace, "B")
    payload = {
        "requestId": str(uuid.uuid4()),
        "kind": "product",
        "sourceId": item.id,
        "referenceIds": ["arbitrary"],
        "target": "x",
    }
    actor(client, "B", "asset:read", "template:use")
    assert client.post("/api/v1/templates/replacement-runs", json=payload).status_code == 400
    actor(client, "B", "asset:read", "template:read")
    assert client.post("/api/v1/templates/replacement-runs", json=payload).status_code == 403


def test_publish_idempotent_version_and_disable(workspace: Path) -> None:
    uid, _ = pack(workspace)
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


def test_legacy_pack_is_not_automatically_public(workspace: Path) -> None:
    uid, _ = pack(workspace)
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
    assert client.get("/api/v1/assets/archive", params={"scope": "everyone"}).status_code == 422
    assert client.get("/api/v1/assets/archive", params={"state": "deleted"}).status_code == 422
    assert client.get("/api/v1/assets/archive", params={"category": "video"}).status_code == 422
