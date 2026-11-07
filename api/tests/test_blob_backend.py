"""两条物理通路的共享后端：一个 client、可单桶可分桶。

以前 storage/s3.py 与 archive/s3.py 各建一份 boto3 client、各声明一份连接参数，
改一处连接配置要改两个文件；`S3_ARCHIVE_CACHE_DIR` 更因为字段名没带前缀，
实际环境变量是 `S3_CACHE_DIR`，文档与代码长期对不上。这里锁住这两个事实。
"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from core.exceptions import UpstreamError
from infra.archive.s3 import ArchiveS3Settings, S3ArchiveStorage
from infra.blob import client_cache_info, reset_client_cache, s3_client


def _fake_factory() -> Any:
    def make(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"args": args, "kwargs": kwargs}

    return make


def test_same_connection_reuses_one_client() -> None:
    reset_client_cache()
    factory = _fake_factory()
    kwargs = dict(
        endpoint="http://127.0.0.1:9000",
        access_key="key",
        secret_key="secret",
        region="us-east-1",
        use_ssl=False,
        verify=False,
        factory=factory,
    )
    first = s3_client(**kwargs)
    second = s3_client(**kwargs)
    assert first is second
    assert client_cache_info()["currsize"] == 1
    assert client_cache_info()["hits"] >= 1


def test_different_connection_gets_its_own_client() -> None:
    reset_client_cache()
    factory = _fake_factory()
    base = dict(
        endpoint="http://127.0.0.1:9000",
        access_key="key",
        secret_key="secret",
        region="us-east-1",
        use_ssl=False,
        verify=False,
        factory=factory,
    )
    first = s3_client(**base)
    second = s3_client(**{**base, "endpoint": "http://other:9000"})
    assert first is not second
    assert client_cache_info()["currsize"] == 2


def _settings(**overrides: Any) -> ArchiveS3Settings:
    return ArchiveS3Settings(
        endpoint="http://127.0.0.1:9000",
        access_key="key",
        secret_key="secret",
        use_ssl=False,
        **overrides,
    )


def test_separate_mode_requires_its_own_bucket() -> None:
    with pytest.raises(UpstreamError, match="S3_ARCHIVE_BUCKET"):
        S3ArchiveStorage(settings=_settings(bucket_mode="separate", archive_bucket=""))


def test_invalid_bucket_mode_is_rejected() -> None:
    with pytest.raises(UpstreamError, match="S3_BUCKET_MODE"):
        S3ArchiveStorage(settings=_settings(bucket_mode="whatever", archive_bucket="arc"))


def test_shared_mode_uses_material_bucket_with_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "infra.storage.s3.get_s3_settings", lambda: SimpleNamespace(bucket="media")
    )
    monkeypatch.setattr("infra.archive.s3.boto3.client", _fake_factory())
    reset_client_cache()
    storage = S3ArchiveStorage(settings=_settings(bucket_mode="shared"))
    assert storage._bucket == "media"
    assert storage._prefix.startswith("archive")
    assert storage._client is not None
    reset_client_cache()


def test_cache_dir_setting_matches_documented_env_key() -> None:
    """`S3_ARCHIVE_CACHE_DIR` 必须真能解析，别再出现「文档写了但代码不认」。"""
    from infra.driver_config import driver_config

    prefix = str(ArchiveS3Settings.model_config.get("env_prefix"))
    assert prefix == "S3_"
    assert "archive_cache_dir" in ArchiveS3Settings.model_fields
    assert "cache_dir" not in ArchiveS3Settings.model_fields
    assert driver_config("S3")["env_prefix"] == "S3_"


# --------------------------------------------------------------------------- #
# 物化缓存的并发安全
# --------------------------------------------------------------------------- #
BLOB = "blobs/ab/" + "ab" * 32


class _BarrierClient:
    """两个下载线程都能「写同一个临时文件」，然后各自 rename —— 真实竞态的交织点。

    取件走 `asyncio.to_thread(self._materialized, key)`，所以同一个进程里的两个
    并发请求是真的在**两个线程**里跑 `materialize`。临时文件名如果只带 pid，
    两个线程会写同一个路径：先完成的那次 rename 把文件搬走，后一个 rename
    找不到自己的临时文件 → FileNotFoundError。症状是「并发取同一张图时偶发 500」。
    """

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.barrier = threading.Barrier(2, timeout=5)

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        return {"Metadata": {}, "ContentLength": len(self.payload)}

    def download_file(self, bucket: str, key: str, path: str) -> None:
        with open(path, "wb") as stream:
            stream.write(self.payload)
        self.barrier.wait()


def _s3_archive(tmp_path: Path, client: Any) -> S3ArchiveStorage:
    reset_client_cache()
    storage = S3ArchiveStorage(
        settings=_settings(archive_bucket="arc", archive_cache_dir=str(tmp_path))
    )
    storage._client = client
    return storage


def test_two_threads_materializing_the_same_key_do_not_clobber_each_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("infra.archive.s3.boto3.client", lambda *a, **k: object())
    storage = _s3_archive(tmp_path, _BarrierClient(b"frames"))

    errors: list[BaseException] = []
    contents: list[bytes] = []

    def work() -> None:
        try:
            with storage.materialize(BLOB) as path:
                contents.append(path.read_bytes())
        except BaseException as exc:  # noqa: BLE001 - 就是要看它抛什么
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    reset_client_cache()

    assert errors == [], f"并发物化同一份字节时失败：{errors!r}"
    assert contents == [b"frames", b"frames"]


def test_cache_eviction_tolerates_files_that_cannot_be_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows 上删不掉「正在被读」的文件（ffmpeg / FileResponse 都持有句柄）。

    淘汰是**尽力而为**：删不掉就跳过，绝不能因此让这次取件失败——缓存的意义是
    省一次下载，不是成为新的故障点。
    """
    from infra.archive import s3 as s3_module

    monkeypatch.setattr("infra.archive.s3.boto3.client", lambda *a, **k: object())
    storage = _s3_archive(tmp_path, _BarrierClient(b"x"))
    (tmp_path / "busy.jpg").write_bytes(b"0" * 8)
    (tmp_path / "idle.jpg").write_bytes(b"0" * 8)
    monkeypatch.setattr(s3_module, "CACHE_LIMIT_BYTES", 4)

    def refuse(self: Path, missing_ok: bool = False) -> None:  # noqa: ARG001
        raise PermissionError(13, "另一个进程正在使用这个文件")

    monkeypatch.setattr(Path, "unlink", refuse)
    storage._evict()  # 不该抛
