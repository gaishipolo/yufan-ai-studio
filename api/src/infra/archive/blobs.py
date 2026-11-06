"""内容寻址的素材字节层：key 就是 sha256。

**为什么要有这一层**：以前素材按「分类 + 年月 + 随机 id」存，位置随机、内容可变。
于是"这份字节还是不是当初那份"只能靠每次读出来重算 sha256 来猜，猜的时机还得
挑（发布前、准备任务时），漏一次就等于历史版本被悄悄换掉。改成 key = sha256 后：

- 字节变了就是另一个 key，不存在"检测字节是否被改"这个动作；
- 同一份字节登记到不同分类、被多个封装引用，物理上只有一份；
- 已发布版本引用的字节永远在，历史可复现不需要额外机制。

`previews/` 同理按素材摘要命名：同一张图不会因为登记两次就生成两份预览，缓存
也不会被素材改名/移动弄脏（旧实现用 asset_id 命名，删了重建就是一次无谓的派生）。
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from core.exceptions import BadRequest, NotFound
from infra.archive.base import ArchiveStorage
from infra.media_probe import PREVIEW_VERSION, create_browser_preview, create_preview

BLOB_ROOT = "blobs"
PREVIEW_ROOT = "previews"
# 预览是派生物，key 里带版本号：换算法（hd-v2 → hd-v3）时旧缓存自然失效，
# 不需要"先清缓存再发布"这种人工步骤。
_PREVIEW_VERSION = PREVIEW_VERSION

_SHA = re.compile(r"[0-9a-f]{64}")
_SUFFIX = re.compile(r"\.[a-z0-9]{1,8}")


def normalize_suffix(suffix: str) -> str:
    """扩展名只留 `.<字母数字>`：它会被拼进 key，不能带分隔符或 `..`。"""
    value = suffix if suffix.startswith(".") else f".{suffix}"
    return value.lower() if _SUFFIX.fullmatch(value.lower()) else ""


def blob_key(sha256: str, suffix: str = "") -> str:
    """`blobs/ab/abcdef…{ext}`：前两位分桶，避免单目录几十万文件。"""
    if not _SHA.fullmatch(sha256):
        raise BadRequest("素材摘要无效")
    return f"{BLOB_ROOT}/{sha256[:2]}/{sha256}{normalize_suffix(suffix)}"


def preview_key(sha256: str) -> str:
    if not _SHA.fullmatch(sha256):
        raise BadRequest("素材摘要无效")
    return f"{PREVIEW_ROOT}/{sha256[:2]}/{sha256}.{_PREVIEW_VERSION}.jpg"


def clip_key(sha256: str) -> str:
    """浏览器可播的轻量视频派生流，按素材摘要命名，同一原片只派生一次。"""
    if not _SHA.fullmatch(sha256):
        raise BadRequest("素材摘要无效")
    return f"{PREVIEW_ROOT}/{sha256[:2]}/{sha256}.{_PREVIEW_VERSION}.mp4"


def is_blob_key(key: str) -> bool:
    """内容寻址布局的 key（迁移与巡检据此区分新旧存放方式）。"""
    return key.startswith(f"{BLOB_ROOT}/")


class BlobStore:
    """素材字节的读写口。

    旧档案的 `path` 还指向 `library/<分类>/<年月>/…`（位置寻址），这里所有方法
    都接受任意 key：内容寻址只是**新写入**的约定，读取一律按 key 直达，
    所以存量记录不需要先迁移也能继续用。
    """

    def __init__(self, storage: ArchiveStorage) -> None:
        self.storage = storage

    def key(self, sha256: str, suffix: str = "") -> str:
        return blob_key(sha256, suffix)

    def preview(self, sha256: str) -> str:
        return preview_key(sha256)

    def exists(self, key: str) -> bool:
        if not key:
            return False
        return self.storage.exists(key)

    def ensure(self, source: Path, sha256: str, suffix: str = "") -> tuple[str, bool]:
        """把本地字节登记为内容寻址对象，返回 `(key, 是否新建)`。

        `created=False` 表示这份字节已经在库里（同一个人重复登记、或别人传过
        一模一样的文件），调用方回滚时**不能**把它删掉——删了会连带打断引用它的
        其它素材。新建才由调用方负责清理。
        """
        key = self.key(sha256, suffix)
        if self.storage.exists(key):
            return key, False
        self.storage.copy(source, key, sha256)
        return key, True

    def adopt(self, source_key: str, sha256: str, suffix: str = "") -> tuple[str, bool]:
        """把已存在于归档里的字节搬进内容寻址布局（迁移用）。"""
        target = self.key(sha256, suffix)
        if target == source_key or self.storage.exists(target):
            return target, False
        with self.storage.materialize(source_key) as path:
            self.storage.copy(path, target, sha256)
        return target, True

    def materialize(self, key: str) -> AbstractContextManager[Path]:
        return _materialize(self.storage, key)

    def derive(self, source_key: str, target_key: str, *, kind: str, clip: bool = False) -> bool:
        """派生预览/转码流并入库，返回是否可用。

        派生在本地临时目录完成，再按 key 上传：远端驱动没有"可写路径"这种东西，
        分叉出"本地直写"的分支只会让两条路径的行为慢慢漂移。
        """
        if self.exists(target_key):
            return True
        suffix = Path(target_key).suffix or ".jpg"
        with (
            self.materialize(source_key) as original,
            tempfile.TemporaryDirectory(prefix="yufan-derive-") as folder,
        ):
            target = Path(folder) / f"derived{suffix}"
            ok = (
                create_browser_preview(original, target)
                if clip
                else create_preview(original, target, kind)
            )
            if not ok or not target.is_file():
                return False
            self.storage.copy(target, target_key)
            return True

    def remove(self, key: str) -> None:
        self.storage.remove(key)


@contextmanager
def _materialize(storage: ArchiveStorage, key: str) -> Iterator[Path]:
    """交出可读路径；不存在的 key 一律收敛成「素材不存在」。

    本地驱动交出的就是归档里的原路径，不会在 `__enter__` 时报错，所以这里显式
    检查一次：调用方（`FileResponse`、ffmpeg、桥接层）拿到的必须是能打开的文件，
    "路径给了但读不到"只会在更下游变成一个难解释的 500。
    """
    try:
        with storage.materialize(key) as path:
            if not path.is_file():
                raise NotFound("素材字节不存在，请重新登记")
            yield path
    except FileNotFoundError as exc:
        raise NotFound("素材字节不存在，请重新登记") from exc
