"""生成产物的引用形态：库里存 key，出参才变成地址。

## 为什么改

上一版把 `url_for()` 的**产物**直接落进 `image_jobs.output_url`：本地驱动下是
`/static/images/xxx.png`。而 `/static` 只在 `STORAGE_DRIVER=local` 时挂载，切到
s3 之后这批历史地址没有任何端点应答——生图页整屏破图，且不改代码修不回来。
换到第三个驱动（OSS / COS）时存量地址会再失联一次。

现在库里只存**对象 key**（`images/<id>.png`），与驱动无关；读的时候按当前驱动
现签。签名统一走 `ObjectStorage.presigned_download_url`：

- 本地驱动返回 `/static/<key>`（`/static` 就是本地存储根，本来就能直取）
- 远端驱动返回带时效的预签名地址，私有桶也能取

调用方两者都当普通 URL 用，不需要知道背后是哪种驱动。

## 历史值怎么办

`http(s)://`、`/static/...`、`data:` 这类**不是 key**，原样返回：它们是供应商直链
（视频任务）或迁移前的老地址。`app migrate-storage-urls` 负责把老的 `/static/<key>`
剥成 key，剥不动的（外站地址）保持原样。
"""

from __future__ import annotations

from collections.abc import Sequence

from core.logging import get_logger
from infra.storage.base import ObjectStorage, StorageError

__all__ = ["ARTIFACT_URL_TTL_SEC", "artifact_url", "artifact_urls", "is_object_key"]

logger = get_logger(__name__)

# 预签名有效期。列表接口每次都重新签，所以这个值只决定「同一份地址能放多久」：
# 太短历史页会在用户停留时裂图，太长等于把私有对象的访问权发放出去。
# 一天是 S3 SigV4 的合理折中（上限 7 天，但没有必要贴近上限）。
ARTIFACT_URL_TTL_SEC = 24 * 60 * 60

# 不是 key 的前缀：供应商直链、迁移前的老静态地址、内联数据
_EXTERNAL_PREFIXES = ("http://", "https://", "//", "/", "data:", "blob:")


def is_object_key(value: str | None) -> bool:
    """这个值是对象 key，还是已经可以直接交给浏览器的地址？"""
    if not value:
        return False
    return not value.startswith(_EXTERNAL_PREFIXES)


async def artifact_urls(
    values: Sequence[str | None], *, expires_in: int = ARTIFACT_URL_TTL_SEC
) -> list[str]:
    """批量把落库引用转成可访问地址；一次性取存储驱动实例。

    签名失败不抛：一张图的地址算不出来，不该让整个历史列表 500。返回空串，
    前端显示占位图——比整页打不开更容易定位。
    """
    if not values:
        return []
    needs_signing = any(is_object_key(value) for value in values)
    storage = _storage() if needs_signing else None

    urls: list[str] = []
    for value in values:
        if not value:
            urls.append("")
            continue
        if not is_object_key(value) or storage is None:
            urls.append(value)
            continue
        try:
            urls.append(await storage.presigned_download_url(value, expires_in=expires_in))
        except StorageError as exc:
            logger.warning("产物地址签名失败，暂不给地址：%s（%s）", value, exc)
            urls.append("")
    return urls


async def artifact_url(value: str | None, *, expires_in: int = ARTIFACT_URL_TTL_SEC) -> str:
    """单个值的转换，语义同 `artifact_urls`。"""
    return (await artifact_urls([value], expires_in=expires_in))[0]


def _storage() -> ObjectStorage:
    from infra import get_storage

    return get_storage()
