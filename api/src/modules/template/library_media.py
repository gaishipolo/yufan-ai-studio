"""把资产库 v2 的参考图搬进本次 run 的工作区。

两套素材库的差别只有「字节从哪来」：

- v1 `StoredMedia.path` 是归档存储里的位置，`ArchiveStorage.materialize()` 直接给路径；
- v2 只有 `storage_key`，字节在 `ObjectStorage`。本地驱动能直出磁盘路径（零拷贝），
  远端驱动必须分块下载进工作区。

**不把 `storage_key` 冒充 `path`**：一个是对象键、一个是归档位置，混用会让
「这份字节到底在哪个存储里」变成不可判的隐式约定，而 `ArchiveStorage.link()`
会拿它去自己的根目录下解析——错得不会报错，只会 404 或写到一个同名文件上。

所以这里产出 `StagedMedia`：**本次 prepare 内有效**的「已可读本地文件 + 元数据」。
它不进数据库、不进 `run.json`；冻结进档案的仍是工作区里的那份副本（`storage.link`），
与 v1 的归档方式一致。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from core.exceptions import BadRequest
from core.logging import get_logger
from core.ports.library_lookup import LibraryAssetKind, LibraryAssetRef
from infra import get_storage
from infra.archive import checksum
from infra.media_probe import EXTENSIONS
from infra.storage.base import ObjectStorage, StorageError

logger = get_logger(__name__)

#: 暂存目录名。放在工作区里而不是系统临时目录：同盘才有硬链接，否则每次
#: prepare 都要把几十 MB 的图完整复制一遍。
STAGING_DIR = ".staging"

_KIND_TYPE = {
    LibraryAssetKind.IMAGE: "image",
    LibraryAssetKind.VIDEO: "video",
    LibraryAssetKind.AUDIO: "audio",
    LibraryAssetKind.TEXT: "text",
    LibraryAssetKind.PACK: "pack",
}


@dataclass(frozen=True, slots=True)
class StagedMedia:
    """一份已经落在本地、可被 `ArchiveStorage.link()` 直接搬走的素材。

    字段刻意与 `StoredMedia` 的子集同名：`_media_in_mixed_order` / `_materialize_inputs`
    按属性取用，两种素材才能走同一条排序与归档路径，而不需要把 v2 元数据硬塞进
    `StoredMedia`（那要求伪造 `category` / `owner_id` / `shared`，等于在模板侧
    重造一套权限语义）。
    """

    id: str
    name: str
    type: str
    sha256: str
    path: Path
    width: int = 0
    height: int = 0
    duration: float = 0
    mime_type: str = ""
    #: 本文件是本次 prepare 下载的临时副本；本地驱动零拷贝时是 `False`，
    #: 那种路径指向内容寻址的 blob，**删掉就是删别人的素材**。
    temporary: bool = False


async def stage_library_refs(
    workspace: Path, refs: Mapping[str, LibraryAssetRef]
) -> dict[str, StagedMedia]:
    """把 `{token: 资产}` 逐条落到本地，返回同键的 `StagedMedia`。

    一张都取不到就整体失败：固定引用是提示词里点名的那几张图，少一张生成的
    成片就换了主体，而这种错误在成片出来之前完全看不出来。
    """
    if not refs:
        return {}
    storage = get_storage()
    staged: dict[str, StagedMedia] = {}
    ok = False
    try:
        for token, ref in refs.items():
            try:
                staged[token] = await _stage_one(storage, workspace, ref)
            except (OSError, StorageError) as exc:
                # 发布时已经 `exists()` 验过一次，这里再缺就是「发布之后被删/桶没挂上」。
                # 不转成业务错误的话，用户看到的是 500「服务器错误」，而真正要做的是
                # 重新登记那张图——报错得指得到具体是哪一张。
                logger.warning("暂存资产库素材失败 key=%s", ref.storage_key, exc_info=True)
                raise BadRequest(f"参考图「{ref.name}」的字节缺失，请重新登记后再试") from exc
        ok = True
    finally:
        # 半途失败（含取消）时，调用方拿不到 `staged`，没法替这里清理：
        # 已经下载好的那几张会一路堆到工作区被归档 GC 扫到为止。
        if not ok:
            discard_staged(staged)
    return staged


def discard_staged(staged: Mapping[str, StagedMedia]) -> None:
    """删掉本次下载的临时副本。零拷贝的 blob 路径不动。"""
    for item in staged.values():
        if not item.temporary:
            continue
        try:
            item.path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - 清理失败不该盖住真正的错误
            logger.warning("暂存素材清理失败 id=%s", item.id)


async def _stage_one(
    storage: ObjectStorage, workspace: Path, ref: LibraryAssetRef
) -> StagedMedia:
    suffix = _suffix(ref)
    local = storage.local_path(ref.storage_key) if ref.storage_key else None
    if local is not None and local.is_file():
        # 本地驱动：blob 本身就可读，直接交给 `link()` 做硬链接。
        path, temporary = local, False
    else:
        path, temporary = await _download(storage, workspace, ref, suffix), True
    return StagedMedia(
        id=ref.id,
        name=ref.name,
        type=_KIND_TYPE.get(ref.kind, "image"),
        sha256=ref.sha256 or checksum(path),
        path=path,
        width=ref.width,
        height=ref.height,
        duration=(ref.duration_ms or 0) / 1000,
        mime_type=ref.mime_type,
        temporary=temporary,
    )


async def _download(
    storage: ObjectStorage, workspace: Path, ref: LibraryAssetRef, suffix: str
) -> Path:
    """分块下载，不把整份字节读进内存（原片 300MB 级别）。

    先写 `.part` 再改名：prepare 中途失败留下的半个文件如果直接叫正式名，
    下一次重试会命中 `ArchiveStorage.link()` 的「已有归档文件与本次输入不一致」。
    """
    folder = workspace / "inputs" / STAGING_DIR
    await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
    target = folder / f"{ref.id}{suffix}"
    partial = target.with_suffix(target.suffix + ".part")
    try:
        handle = await asyncio.to_thread(partial.open, "wb")
        try:
            async for chunk in storage.iter_chunks(ref.storage_key):
                await asyncio.to_thread(handle.write, chunk)
        finally:
            await asyncio.to_thread(handle.close)
        await asyncio.to_thread(partial.replace, target)
    except Exception:
        await asyncio.to_thread(partial.unlink, True)
        await asyncio.to_thread(target.unlink, True)
        raise
    return target


def _suffix(ref: LibraryAssetRef) -> str:
    """工作区文件名要带**正确**扩展名：执行器按扩展名判类型上传。

    先看 `storage_key`（内容寻址的键自带真实扩展名），再退回 mime 映射。
    两者都不认就报错——猜一个 `.bin` 会让上传后的素材类型变成未知，
    错误要到供应商那侧才爆出来。
    """
    suffix = Path(ref.storage_key).suffix.lower()
    if suffix in EXTENSIONS:
        return suffix
    mime = (ref.mime_type or "").split(";")[0].strip().lower()
    if mime:
        for extension, declared in EXTENSIONS.items():
            if declared.split(";")[0].strip().lower() == mime:
                return extension
    raise BadRequest("参考图格式无法识别，请重新上传后再试")
