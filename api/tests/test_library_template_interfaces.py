"""资产库侧为「模板库将来切换」补的接口（设计 §4 取件端点 / §10.4 并存期）。

本轮只补后端，前端与模板库一行不动；切换时改 `archive.api.ts` 的调用地址即可。
四个必须守住的点：

1. `origin` 过滤要同时取到 `template_source`（切换后新传）与 `imported`
   （并存期从旧库导入的 7 条存量原片），列表与选择器同口径；
2. 列表 / 详情自带三个取件 URL 与宽高，前端不用再按 id 拼第二套路径；
3. `/stream` 派生只做一次、绝不改原片；非视频 / 回收站 / 不可见一律拒；
4. `/preview` 视频给派生流、图片给原文件；派生失败时不拿**别人**的原件冒充预览。
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from fastapi.testclient import TestClient
from library_support import (
    LIB_RW,
    admin_headers,
    insert_asset,
    make_user,
    put_blob,
    upload_image,
)

ORIGINAL = b"ORIGINAL-10BIT-MP4-BYTES"
CLIP = b"H264-8BIT-PREVIEW-MP4"


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _video_asset(
    owner_id: str,
    *,
    origin: str,
    digest: str,
    visibility: str = "personal",
    state: str = "active",
    width: int = 1080,
    height: int = 1920,
) -> tuple[str, str]:
    """直接造一条视频资产：走上传接口会被 ffprobe 拦（假字节不是真 mp4）。"""
    from modules.library.service import blob_key

    key = blob_key(digest, ".mp4")
    put_blob(key, ORIGINAL, "video/mp4")
    asset_id = insert_asset(
        name="模板原片.mp4",
        kind="video",
        origin=origin,
        mime_type="video/mp4",
        size_bytes=len(ORIGINAL),
        storage_key=key,
        sha256=digest,
        width=width,
        height=height,
        duration=8.0,
        has_audio=True,
        owner_id=owner_id,
        visibility=visibility,
        state=state,
    )
    return asset_id, key


def _fake_ffmpeg(monkeypatch, succeed: bool = True) -> list[bytes]:
    """替掉真转码：只关心「读到的原片是什么」与「派生了几次」。"""
    reads: list[bytes] = []

    def fake(source: Path, destination: Path) -> bool:
        reads.append(source.read_bytes())
        if succeed:
            destination.write_bytes(CLIP)
        return succeed

    monkeypatch.setattr("modules.library.service.create_browser_preview", fake)
    return reads


def _ids(payload: dict) -> set[str]:
    return {item["id"] for item in payload["items"]}


def test_origin_filter_covers_new_and_imported_sources(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner, headers = make_user(client, admin, LIB_RW)
    new_one, _ = _video_asset(owner["id"], origin="template_source", digest=_digest("new"))
    imported, _ = _video_asset(owner["id"], origin="imported", digest=_digest("old"))
    plain = upload_image(client, api, headers, name="普通上传.png")

    listed = client.get(
        f"{api}/library/assets",
        params={"state": "active", "library": "personal", "pageSize": 100},
        headers=headers,
    )
    assert listed.status_code == 200, listed.text
    assert {new_one, imported, plain["id"]} <= _ids(listed.json()["data"])

    filtered = client.get(
        f"{api}/library/assets",
        params={
            "state": "active",
            "library": "personal",
            "origin": ["template_source", "imported"],
        },
        headers=headers,
    )
    assert filtered.status_code == 200, filtered.text
    assert _ids(filtered.json()["data"]) == {new_one, imported}
    assert filtered.json()["data"]["total"] == 2

    single = client.get(
        f"{api}/library/assets",
        params={"state": "active", "library": "personal", "origin": "template_source"},
        headers=headers,
    )
    assert _ids(single.json()["data"]) == {new_one}

    selected = client.get(
        f"{api}/library/selection",
        params={"origin": ["template_source", "imported"]},
        headers=headers,
    )
    assert selected.status_code == 200, selected.text
    assert _ids(selected.json()["data"]) == {new_one, imported}


def test_list_and_detail_carry_fetch_urls_and_media_size(
    client: TestClient, api: str
) -> None:
    admin = admin_headers(client)
    owner, headers = make_user(client, admin, LIB_RW)
    video, _ = _video_asset(
        owner["id"], origin="template_source", digest=_digest("urls")
    )
    plain = upload_image(client, api, headers, name="普通上传.png")

    page = client.get(
        f"{api}/library/assets",
        params={"state": "active", "library": "personal"},
        headers=headers,
    ).json()["data"]
    by_id = {item["id"]: item for item in page["items"]}

    item = by_id[video]
    assert item["url"].endswith(f"/library/assets/{video}/file")
    assert item["streamUrl"].endswith(f"/library/assets/{video}/stream")
    assert item["previewUrl"] is None, "没封面就不该给一个必然 404 的地址"
    assert item["width"] == 1080 and item["height"] == 1920

    image = by_id[plain["id"]]
    assert image["url"].endswith(f"/library/assets/{plain['id']}/file")
    assert image["streamUrl"] is None, "图片没有视频预览流"
    assert image["previewUrl"].endswith(f"/library/assets/{plain['id']}/thumbnail")

    detail = client.get(f"{api}/library/assets/{video}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["streamUrl"] == item["streamUrl"]
    assert detail.json()["data"]["width"] == 1080


def test_stream_derives_once_and_never_touches_the_original(
    client: TestClient, api: str, monkeypatch
) -> None:
    from infra import get_storage
    from modules.library.service import clip_key

    admin = admin_headers(client)
    owner, headers = make_user(client, admin, LIB_RW)
    digest = _digest("stream")
    video, key = _video_asset(
        owner["id"], origin="template_source", digest=digest
    )
    reads = _fake_ffmpeg(monkeypatch)

    first = client.get(f"{api}/library/assets/{video}/stream", headers=headers)
    assert first.status_code == 200, first.text
    assert first.headers["content-type"].startswith("video/mp4")
    assert first.content == CLIP
    assert reads == [ORIGINAL], "派生必须读原片，但不能改写它"
    assert asyncio.run(get_storage().get(key)) == ORIGINAL
    assert asyncio.run(get_storage().exists(clip_key(digest))) is True

    second = client.get(f"{api}/library/assets/{video}/stream", headers=headers)
    assert second.status_code == 200, second.text
    assert second.content == CLIP
    assert len(reads) == 1, "同一份字节只该派生一次"


def test_stream_rejects_non_video_trash_and_strangers(
    client: TestClient, api: str, monkeypatch
) -> None:
    admin = admin_headers(client)
    owner, headers = make_user(client, admin, LIB_RW)
    stranger, stranger_h = make_user(client, admin, LIB_RW)
    _fake_ffmpeg(monkeypatch)

    plain = upload_image(client, api, headers, name="图片.png")
    assert (
        client.get(f"{api}/library/assets/{plain['id']}/stream", headers=headers).status_code
        == 404
    )

    video, _ = _video_asset(
        owner["id"], origin="template_source", digest=_digest("guards")
    )
    assert (
        client.get(f"{api}/library/assets/{video}/stream", headers=stranger_h).status_code
        == 404
    )

    deleted = client.delete(f"{api}/library/assets/{video}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert (
        client.get(f"{api}/library/assets/{video}/stream", headers=headers).status_code
        == 404
    )
    assert stranger["id"] != owner["id"]


def test_preview_serves_derived_clip_for_video_and_original_for_image(
    client: TestClient, api: str, monkeypatch
) -> None:
    admin = admin_headers(client)
    owner, headers = make_user(client, admin, LIB_RW)
    video, _ = _video_asset(
        owner["id"], origin="imported", digest=_digest("preview-video")
    )
    plain = upload_image(client, api, headers, name="预览图.png", color="teal")
    _fake_ffmpeg(monkeypatch)

    movie = client.get(f"{api}/library/assets/{video}/preview", headers=headers)
    assert movie.status_code == 200, movie.text
    assert movie.content == CLIP, "视频的公共预览必须是派生流，不是整份原件"

    image = client.get(f"{api}/library/assets/{plain['id']}/preview", headers=headers)
    assert image.status_code == 200, image.text
    assert image.headers["content-type"].startswith("image/png")
    assert image.content, "图片预览给原图"


def test_preview_never_falls_back_to_someone_elses_original(
    client: TestClient, api: str, monkeypatch
) -> None:
    """派生失败时本人可以拿原件，别人只能拿到明确的失败，不能悄悄放行下载。"""
    admin = admin_headers(client)
    owner, headers = make_user(client, admin, LIB_RW)
    _, stranger_h = make_user(client, admin, LIB_RW)
    video, _ = _video_asset(
        owner["id"],
        origin="template_source",
        digest=_digest("derive-fails"),
        visibility="team",
    )
    _fake_ffmpeg(monkeypatch, succeed=False)

    other = client.get(f"{api}/library/assets/{video}/preview", headers=stranger_h)
    assert other.status_code == 400, other.text

    mine = client.get(f"{api}/library/assets/{video}/preview", headers=headers)
    assert mine.status_code == 200, mine.text
    assert mine.content == ORIGINAL
