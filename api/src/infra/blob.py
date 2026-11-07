"""两条物理通路共享的字节后端原语。

`ObjectStorage`（用户上传 / 生成结果）与 `ArchiveStorage`（归档原片 / 版本快照）
语义不同，协议也故意不同名；但它们**物理上都只是 S3 的 key→bytes**。以前各自的
驱动都自己 `boto3.client(...)`、各声明一份 `S3_*` 连接参数，结果是两个 client、
两套超时/重试行为、以及「改一处连接配置要改两个文件」。

这里把「建连接」收敛成一个可缓存的工厂：两个驱动仍各自读自己的 Settings 字段
（配置守卫要求字段在声明它的模块里被使用），但按连接参数复用同一个 client。
新增第三种业务通路时，不必再造一个 boto3 客户端。

`S3_BUCKET_MODE=shared` 时两条通路还可以共用一个桶，靠 key 前缀隔离；默认仍是
`separate`（归档桶私有、素材桶可读），避免把私有原片放进公开读范围。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

__all__ = ["S3Connection", "client_cache_info", "s3_client"]


@dataclass(frozen=True, slots=True)
class S3Connection:
    """一组完整的 S3 连接参数；值相等的两条通路共享同一个 client。"""

    endpoint: str
    access_key: str
    secret_key: str
    region: str
    use_ssl: bool
    verify: bool


@lru_cache(maxsize=16)
def _cached_client(connection: S3Connection, factory: Callable[..., Any]) -> Any:
    # boto3 延迟导入：用 local 驱动部署的机器不该为了启动自检去加载它
    import boto3
    from botocore.client import Config

    make = factory if factory is not None else boto3.client
    return make(
        "s3",
        endpoint_url=connection.endpoint or None,
        aws_access_key_id=connection.access_key or None,
        aws_secret_access_key=connection.secret_key or None,
        region_name=connection.region,
        use_ssl=connection.use_ssl,
        verify=connection.verify,
        # MinIO 必须 path-style，否则虚拟主机风格会把桶名当域名解析
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def s3_client(
    *,
    endpoint: str,
    access_key: str,
    secret_key: str,
    region: str,
    use_ssl: bool,
    verify: bool,
    factory: Callable[..., Any] | None = None,
) -> Any:
    """按连接参数取共享 client。`factory` 供测试注入假的 boto3.client。

    `factory` 参与缓存键：注入替身时不会拿到上一个用例缓存下来的真 client。
    """
    import boto3

    connection = S3Connection(
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        region=region,
        use_ssl=use_ssl,
        verify=verify,
    )
    return _cached_client(connection, factory or boto3.client)


def client_cache_info() -> dict[str, int]:
    """部署自检用：确认两条通路确实复用了 client（currsize 应为 1）。"""
    info = _cached_client.cache_info()
    return {"hits": info.hits, "misses": info.misses, "currsize": info.currsize}


def reset_client_cache() -> None:
    """仅测试用：清空共享 client 缓存。"""
    _cached_client.cache_clear()
