"""归档字节完整性巡检：`key = sha256` 既是命名也是校验和。

`app verify-blobs` 与 `app doctor` 共用这份实现，避免两处各写一遍抽样逻辑、
最后「启动自检说没问题、独立命令说坏了」。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from infra.archive import ArchiveStorage, BlobStore

__all__ = ["IntegrityReport", "verify_blobs"]


@dataclass
class IntegrityReport:
    total: int = 0
    checked: int = 0
    ok: int = 0
    skipped: int = 0
    missing: list[str] = field(default_factory=list)
    corrupted: list[str] = field(default_factory=list)

    @property
    def bad(self) -> list[str]:
        return self.missing + self.corrupted

    @property
    def healthy(self) -> bool:
        return not self.bad

    def summary(self) -> str:
        return (
            f"字节 {self.total} 个，抽检 {self.checked} 个：完好 {self.ok}、"
            f"缺失 {len(self.missing)}、摘要不符 {len(self.corrupted)}、跳过 {self.skipped}"
        )


def _sample(keys: list[str], limit: int | None) -> list[str]:
    """确定性均匀抽样：同一批数据每次跑得到同一个集合，报告才能对比。"""
    if limit is None or limit <= 0 or len(keys) <= limit:
        return keys
    step = len(keys) / limit
    return [keys[min(len(keys) - 1, int(index * step))] for index in range(limit)]


def verify_blobs(
    storage: ArchiveStorage, *, sample: int | None = None
) -> IntegrityReport:
    """`sample=None` 表示全量。旧布局的 key 没有摘要，计入 skipped 而非通过。"""
    blobs = BlobStore(storage)
    keys = sorted(storage.list_keys("blobs"))
    report = IntegrityReport(total=len(keys))
    for key in _sample(keys, sample):
        result = blobs.verify(key)
        report.checked += 1
        if result is None:
            report.skipped += 1
        elif result:
            report.ok += 1
        elif storage.exists(key):
            report.corrupted.append(key)
        else:
            report.missing.append(key)
    return report
