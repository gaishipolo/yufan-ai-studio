"""远端归档驱动的取件形态：307 重定向而不是把字节中转进进程（T19）。

本地驱动验证的是「200 + Range」（见 `test_archive_preview.py`），这里验证同一条
路由在**有直连地址**的驱动上会换一种出参。用包装器把本地驱动伪装成远端形态，
而不是 mock 整个 service：要验证的正是「service 拿到地址后怎么回」。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from tests_archive_helpers import archive

from api.deps import SessionDep
from infra.archive.local import LocalArchiveStorage, get_archive_settings
from modules.asset.archive_dependencies import get_archive_service
from modules.asset.archive_service import ArchiveAssetService


class RedirectingStorage:
    """「远端形态」的归档驱动：有预签名地址，但没有可直出的本地路径。

    其余方法全部转发给本地驱动——本文件关心的是取件分支，不是存储语义。
    """

    def __init__(self, inner: LocalArchiveStorage) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def download_url(self, location: str, *, expires_in: int = 900) -> str | None:
        return f"https://archive.example.test/{location}?X-Amz-Signature=stub"


@pytest.fixture
def archive_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(get_archive_settings(), "root", str(tmp_path))
    return tmp_path


@pytest.fixture
def redirecting(client: TestClient):
    """把归档服务换成「远端驱动」，测试结束务必还原：client 是会话级夹具。"""

    def build(session: SessionDep) -> ArchiveAssetService:
        return ArchiveAssetService(session, storage=RedirectingStorage(LocalArchiveStorage()))

    client.app.dependency_overrides[get_archive_service] = build  # type: ignore[attr-defined]
    try:
        yield
    finally:
        client.app.dependency_overrides.pop(get_archive_service, None)  # type: ignore[attr-defined]


def _upload_png(client: TestClient, root: Path) -> dict:
    image = root / "reference.png"
    Image.new("RGB", (16, 16), tuple(os.urandom(3))).save(image, format="PNG")
    return archive.upload(client, "reference.png", "character", image.read_bytes())


def test_remote_driver_redirects_to_presigned_url(
    client: TestClient, archive_root: Path, redirecting: None
) -> None:
    media = _upload_png(client, archive_root)
    response = client.get(f"/api/v1/assets/archive/{media['id']}/file", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("https://archive.example.test/")
    # 地址指向内容寻址的 key，不是本地盘符：桶是私有的，只有带签名的地址能用
    assert media["sha256"] in location


def test_remote_driver_still_404s_when_bytes_are_gone(
    client: TestClient, archive_root: Path, redirecting: None
) -> None:
    """预签名不校验存在性：少了这次 HEAD，用户会拿到一个 307 到 NoSuchKey。"""
    media = _upload_png(client, archive_root)
    (archive_root / media["path"]).unlink()
    response = client.get(f"/api/v1/assets/archive/{media['id']}/file", follow_redirects=False)
    assert response.status_code == 404
