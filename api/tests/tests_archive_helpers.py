"""归档盘夹具：只碰文件，不碰索引。

索引自 `c1d9e4b7a260` 起在 `assets` 表里，素材的创建走 HTTP 上传
（`/api/v1/assets/archive/upload`），测试不再从服务层直接调同步方法 ——
那是旧「JSON 即索引」时代的写法，留着会掩盖真正的入库路径。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from infra.archive import checksum, within
from infra.archive.local import LocalArchiveStorage


class ArchiveFixtures:
    checksum = staticmethod(checksum)
    within = staticmethod(within)

    def root(self) -> Path:
        return LocalArchiveStorage().root()

    def read(self, path: Path) -> dict:
        value = LocalArchiveStorage().read(path)
        assert isinstance(value, dict)
        return value

    def save(self, path: Path, value: object) -> None:
        LocalArchiveStorage().write(path, value)

    def patch(self, path: Path, **changes: object) -> dict:
        """改已有索引的若干字段。

        HTTP 出参是 `ArchivedAsset`（不含 ownerId），直接整份写回会让替换模板
        读到不完整的记录；测试要改的是「盘上的那份」，所以读-改-写。
        """
        value = self.read(path) if path.is_file() else {}
        value.update(changes)
        self.save(path, value)
        return value

    def upload(self, client: TestClient, filename: str, category: str, content: bytes) -> dict:
        """走真实上传接口登记素材，返回出参（与前端拿到的完全一致）。"""
        response = client.post(
            "/api/v1/assets/archive/upload",
            params={"filename": filename, "category": category},
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert response.status_code == 201, response.text
        data = response.json()["data"]
        assert isinstance(data, dict)
        return data


archive = ArchiveFixtures()
