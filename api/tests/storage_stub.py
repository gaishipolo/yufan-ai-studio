"""测试用的目录式对象存储桩。

素材走 OSS 通道时，字节是从**我们的存储**读的（不是归档工作区里那份快照），
所以凡是测这条路的测试都要有一份「存储」。用目录模拟最省事：测试要改字节时
直接改文件，不用另造一套内存桩。
"""

from __future__ import annotations

from pathlib import Path

from infra.storage.base import StorageError


class DirStorage:
    """把一个目录当对象存储：key → 目录下的一个文件。"""

    def __init__(self, root: Path) -> None:
        self._root = root

    async def get(self, key: str) -> bytes:
        path = self._root / key
        if not path.is_file():
            raise StorageError(f"对象不存在：{key}")
        return path.read_bytes()
