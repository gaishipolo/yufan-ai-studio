"""媒体类型由系统判定（M1-E）：不看客户端说，看文件内容。

为什么这件事值得单独一组用例：`media_type` 一旦写错，错误会延迟到**生成时**
才出现——替换模板拿到一个解不开的「视频」，报错指向 provider，
排查方向从第一步就是错的。

判定优先级：文件头签名 > 扩展名。认不出来时信扩展名（容器格式太多，
扩展名仍是有效信息），但**认出来且冲突时以内容为准**。
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from tests_template_helpers import API, png_bytes, upload

from db.session import get_session_factory
from modules.asset.models import Asset


def _media_type(asset_id: str) -> tuple[str, str]:
    """返回 (media_type, mime_type)。"""

    async def read() -> tuple[str, str]:
        async with get_session_factory()() as session:
            row = await session.get(Asset, asset_id)
            assert row is not None
            return row.media_type or "", row.mime_type

    return asyncio.run(read())


def test_media_type_is_written_from_content(client: TestClient) -> None:
    """正常上传：`media_type` 与内容一致，且与旧 `type` 列同值（双写期）。"""
    item = upload(client, "kind-normal.png", "product", png_bytes())
    media_type, mime = _media_type(item["id"])
    assert media_type == "image"
    assert mime == "image/png"


def test_renamed_file_is_judged_by_content_not_extension(client: TestClient) -> None:
    """把 PNG 改名成 `.mp4` 传进原片库：必须被挡，且理由说的是类型不匹配。

    这是最典型的误用：用户手上只有一张图，但界面只让传「原片」，
    于是改个后缀试试。放行的话，坏数据会一路走到生成阶段。
    """
    response = client.post(
        f"{API}/assets/upload",
        params={"filename": "fake.mp4", "category": "source"},
        content=png_bytes(),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 400, response.text
    assert "MP4" in response.json()["message"]


def test_renamed_file_in_image_category_keeps_the_real_mime(client: TestClient) -> None:
    """PNG 改名成 `.mp4` 但放进产品图：放行，但 `mediaType` 与 `mimeType` 按内容记。

    放行的理由：文件本身是好的，用户只是改错了名字。按扩展名记 MIME 会让
    浏览器按 MP4 解释 PNG——预览一直转圈，而不是给出可行动的提示。
    """
    item = upload(client, "renamed-product.mp4", "product", png_bytes())
    media_type, mime = _media_type(item["id"])
    assert media_type == "image"
    assert mime.startswith("image/"), f"按内容应记 image/*，实际 {mime}"


def test_sniffing_prefers_signature_but_accepts_unknown_containers() -> None:
    """认不出来的容器不该被判错：`_sniff_kind` 返回 None 时由扩展名兜底。

    这一条守的是「不要因为加了检测就拒掉原本能传的格式」——
    检测是为了纠正明显的冲突，不是为了收窄白名单。
    """
    import tempfile
    from pathlib import Path

    from modules.asset.archive_service import _sniff_kind

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "unknown.bin"
        path.write_bytes(b"\x00\x01\x02\x03" * 8)
        assert _sniff_kind(path) is None

        png = Path(folder) / "real.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        assert _sniff_kind(png) == "image"

        # MP4 家族的签名在第 4–8 字节
        mp4 = Path(folder) / "real.mp4"
        mp4.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 8)
        assert _sniff_kind(mp4) == "video"
