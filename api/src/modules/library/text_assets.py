"""文本资产：站内新建 / 在线编辑 / 版本历史 / 纯文本取件。

方案 B 的落地：文本不是另起一张「文案表」，而是 `kind=text` 的资产行，
复用去重、权限、可见性、回收站、打包下载与取件。唯一新增的表是
`library_text_versions`，只记录**不可变**的正文快照。

与 `service.py` 的分工：那边管「所有文件型资产共用的读与上传」，这边管
「正文会变」的部分。编辑正文要换 blob key、写版本行、改四个列，塞进
`LibraryService.upload` 旁边只会让那条 300MB 流式链更难读。

## 三条口径

1. **正文一律纯文本**：`.md` 不转 HTML，取件固定 `text/plain; charset=utf-8`
   + `nosniff`；前端详情用 `<pre>`，编辑器用 `<textarea>`。
2. **版本只增不删**：恢复旧版本 = 再写一版，不改历史行。否则「第 3 版当时
   写了什么」会被一次恢复操作改写，快照失去证据意义。
3. **正文快照行内留存**：blob 的 GC 按 `library_assets.storage_key` 数引用，
   编辑会换 key，旧版本若只剩版本行里的 key 会被当孤儿删掉。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import CurrentUser
from core.exceptions import Conflict, NotFound
from core.serving import Served
from infra.media_probe import EXTENSIONS, MAX_TEXT_BYTES, encode_text, text_excerpt
from infra.storage.base import ObjectStorage
from modules.library import access
from modules.library.errors import (
    AssetNotEditable,
    LibraryTextInvalid,
    LibraryTextTooLarge,
)
from modules.library.models import LibraryAsset, LibraryTextVersion
from modules.library.schema import (
    TextAssetIn,
    TextAssetOut,
    TextAssetRestoreIn,
    TextAssetUpdateIn,
    TextAssetVersionOut,
)
from modules.library.service import LibraryService, blob_key, keep_extension
from modules.library.tags import set_asset_tags

__all__ = ["TextAssetService"]

_TEXT_EXTENSIONS = (".txt", ".md")


def _normalize(content: str) -> tuple[str, bytes]:
    """正文规范化 → 严格 UTF-8 字节。

    剪贴板粘贴常带一个 U+FEFF；它算进正文会变成提示词开头一个不可见字符，
    而且同一段文字带不带 BOM 会算出两个 sha256，秒传与版本去重都失效。
    """
    if content.startswith("\ufeff"):
        content = content[1:]
    if not content.strip():
        raise LibraryTextInvalid("文本内容不能为空")
    # NUL 与「非法 UTF-8」归成一类：两者都是**存不进数据库**的正文，
    # 而正文入库失败会留下没人引用的 blob（上传链先 put 后落库）。
    # 拦在落盘之前，用户看到的是「你的文本有问题」而不是 500。
    if "\x00" in content:
        raise LibraryTextInvalid("文本里含有 U+0000 空字符，无法保存，请删除后重试")
    try:
        data = encode_text(content)
    except UnicodeEncodeError as exc:
        raise LibraryTextInvalid("文本内容必须是合法的 UTF-8 字符") from exc
    if len(data) > MAX_TEXT_BYTES:
        raise LibraryTextTooLarge(details={"maxBytes": MAX_TEXT_BYTES})
    return content, data


def _extension_of(row: LibraryAsset) -> str:
    extension = (Path(row.name).suffix or Path(row.storage_key or "").suffix).lower()
    return extension if extension in _TEXT_EXTENSIONS else ".txt"


def _version_out(
    version: LibraryTextVersion, *, with_content: bool = False
) -> TextAssetVersionOut:
    return TextAssetVersionOut(
        version=version.version,
        sha256=version.sha256,
        size_bytes=version.size_bytes,
        excerpt=text_excerpt(version.content),
        note=version.note,
        created_by=version.created_by,
        created_at=version.created_at,
        content=version.content if with_content else None,
    )


class TextAssetService:
    def __init__(self, session: AsyncSession, storage: ObjectStorage) -> None:
        self.session = session
        self.storage = storage

    # ------------------------------------------------------------------ 读

    async def read(self, asset_id: str, actor: CurrentUser) -> TextAssetOut:
        row = await self._text_row(asset_id, actor)
        return self._out(row, await self._latest(row.id))

    async def versions(
        self, asset_id: str, actor: CurrentUser
    ) -> list[TextAssetVersionOut]:
        await self._text_row(asset_id, actor)
        rows = (
            await self.session.scalars(
                select(LibraryTextVersion)
                .where(LibraryTextVersion.asset_id == asset_id)
                .order_by(LibraryTextVersion.version.desc())
            )
        ).all()
        return [_version_out(row) for row in rows]

    async def version(
        self, asset_id: str, number: int, actor: CurrentUser
    ) -> TextAssetVersionOut:
        await self._text_row(asset_id, actor)
        row = await self._version_row(asset_id, number)
        return _version_out(row, with_content=True)

    async def raw(self, asset_id: str, actor: CurrentUser) -> Served:
        """纯文本取件。直接复用 `LibraryService.file`：那边已经把文本固定成
        `text/plain; charset=utf-8` 并带上 `nosniff`，这里再写一份只会漂。"""
        await self._text_row(asset_id, actor)
        return await LibraryService(self.session, self.storage).file(asset_id, actor)

    # ------------------------------------------------------------------ 写

    async def create(self, payload: TextAssetIn, actor: CurrentUser) -> TextAssetOut:
        extension = f".{payload.extension}"
        mime = EXTENSIONS[extension]
        content, data = _normalize(payload.content)
        digest = hashlib.sha256(data).hexdigest()

        # 站内新建也要查重。唯一索引 `uq_library_assets_owner_sha_kind` 拦的是
        # 「同 owner + 同内容 + 同类型」，但撞它时用户看到的是通用 409
        # 「请检查唯一性或关联引用」——他没做错任何事，只是把同一段文字又存了一遍。
        # 上传链有 `_find_duplicate` 先查后写，站内新建当时没跟上。
        duplicate = await self._find_duplicate(actor.id, digest, "text")
        if duplicate is not None:
            # 秒传口径：**直接返回已有那条**，不新建第二个副本。
            # 用户要的是「这份文案在库里」，不是「库里必须有第二份一模一样的」。
            # 名字不同也不另建：同一段正文存两份，等于之后编辑其中一份时
            # 另一份悄悄成了旧版，引用它的提示词也就对不上了。
            return self._out(duplicate, await self._latest(duplicate.id))

        key = blob_key(digest, extension)
        await self.storage.put(key, data, mime)
        # 后缀由 `extension` 决定，用户写在名字里的后缀一律剥掉重拼——
        # 否则「提示词.html」会落进名字里，下载后被浏览器当页面打开。
        name = f"{keep_extension('', '', payload.name)}{extension}"
        row = LibraryAsset(
            name=name[:200],
            kind="text",
            origin="upload",
            mime_type=mime,
            size_bytes=len(data),
            sha256=digest,
            storage_key=key,
            cover_key="",
            width=0,
            height=0,
            duration=0,
            has_audio=False,
            text_excerpt=text_excerpt(content),
            text_version=1,
            owner_id=actor.id,
            visibility="personal",
            state="active",
            state_version=1,
            created_at=datetime.now(UTC),
        )
        self.session.add(row)
        await self.session.flush()
        version = LibraryTextVersion(
            asset_id=row.id,
            version=1,
            content=content,
            sha256=digest,
            size_bytes=len(data),
            note="站内新建",
            created_by=actor.id,
        )
        self.session.add(version)
        if payload.tag_ids:
            await set_asset_tags(self.session, row.id, payload.tag_ids)
        await self.session.commit()
        return self._out(row, version)

    async def update(
        self, asset_id: str, payload: TextAssetUpdateIn, actor: CurrentUser
    ) -> TextAssetOut:
        """在线编辑：换 blob key、追加一版、推进 `text_version`。

        `expectedVersion` 比的是**内容版本**而不是 `stateVersion`：两个人同时
        打开编辑框，后保存的人必须看到「正文已被改过」而不是静默覆盖。
        """
        row = await self._text_row(asset_id, actor, writable=True)
        self._check_version(row, payload.expected_version)
        content, data = _normalize(payload.content)
        digest = hashlib.sha256(data).hexdigest()
        if digest == row.sha256:
            # 内容没变就不制造一个空版本：用户只是点了保存，历史里不该多一版。
            return self._out(row, await self._latest(row.id))

        # 编辑到「自己名下另一条一模一样的正文」同样会撞唯一索引。
        # 这里必须拦在写库之前：撞了索引只有通用 409，用户看不出是自己
        # 撞了库里已有的那份。指路到那一条，他改成引用或先删掉即可。
        duplicate = await self._find_duplicate(actor.id, digest, "text")
        if duplicate is not None and duplicate.id != row.id:
            raise Conflict(
                f"库里已经有一份内容相同的文本素材「{duplicate.name}」，"
                "不能保存成两份一模一样的正文",
                details={"duplicateId": duplicate.id, "duplicateName": duplicate.name},
            )

        extension = _extension_of(row)
        key = blob_key(digest, extension)
        await self.storage.put(key, data, EXTENSIONS[extension])
        number = row.text_version + 1
        version = LibraryTextVersion(
            asset_id=row.id,
            version=number,
            content=content,
            sha256=digest,
            size_bytes=len(data),
            note=payload.note or "在线编辑",
            created_by=actor.id,
        )
        self.session.add(version)
        row.storage_key = key
        row.mime_type = EXTENSIONS[extension]
        row.sha256 = digest
        row.size_bytes = len(data)
        row.text_excerpt = text_excerpt(content)
        row.text_version = number
        row.state_version += 1
        await self.session.commit()
        return self._out(row, version)

    async def restore(
        self, asset_id: str, payload: TextAssetRestoreIn, actor: CurrentUser
    ) -> TextAssetOut:
        """把某历史版本**重新写成新的一版**，历史行保持只读。"""
        await self._text_row(asset_id, actor, writable=True)
        target = await self._version_row(asset_id, payload.version)
        return await self.update(
            asset_id,
            TextAssetUpdateIn(
                content=target.content,
                expected_version=payload.expected_version,
                note=f"恢复第 {target.version} 版",
            ),
            actor,
        )

    # ------------------------------------------------------------------ 内部

    async def _text_row(
        self, asset_id: str, actor: CurrentUser, *, writable: bool = False
    ) -> LibraryAsset:
        row = await access.load_visible(self.session, asset_id, actor)
        if row.kind != "text":
            raise LibraryTextInvalid("该资产不是文本资产")
        if writable:
            if row.state != "active":
                raise AssetNotEditable()
            if row.owner_id != actor.id and not actor.has(access.PERM_DELETE_ANY):
                # 与 `LibraryService.patch` 同口径：能看不等于能改。
                raise NotFound("资产不存在")
        return row

    async def _find_duplicate(
        self, owner_id: str, digest: str, kind: str
    ) -> LibraryAsset | None:
        """同 owner + 同内容 + 同类型、未删除的那条。

        口径必须与 `LibraryService._find_duplicate` **一字不差**：同一个唯一
        索引 `uq_library_assets_owner_sha_kind` 在两处兜底，任何一边多算/
        少算都会出现「查重说没有、写入却撞索引」。
        """
        return await self.session.scalar(
            select(LibraryAsset).where(
                LibraryAsset.owner_id == owner_id,
                LibraryAsset.sha256 == digest,
                LibraryAsset.kind == kind,
                LibraryAsset.deleted_at.is_(None),
            )
        )

    async def _latest(self, asset_id: str) -> LibraryTextVersion:
        row = await self.session.scalar(
            select(LibraryTextVersion)
            .where(LibraryTextVersion.asset_id == asset_id)
            .order_by(LibraryTextVersion.version.desc())
            .limit(1)
        )
        if row is None:
            raise LibraryTextInvalid("文本版本记录缺失，请重新上传")
        return row

    async def _version_row(self, asset_id: str, number: int) -> LibraryTextVersion:
        row = await self.session.scalar(
            select(LibraryTextVersion).where(
                LibraryTextVersion.asset_id == asset_id,
                LibraryTextVersion.version == number,
            )
        )
        if row is None:
            raise NotFound("该版本不存在")
        return row

    @staticmethod
    def _check_version(row: LibraryAsset, expected: int) -> None:
        if expected and row.text_version != expected:
            raise Conflict(
                "正文已被修改，请刷新后重试",
                details={"expectedVersion": expected, "textVersion": row.text_version},
            )

    @staticmethod
    def _out(row: LibraryAsset, version: LibraryTextVersion) -> TextAssetOut:
        return TextAssetOut(
            id=row.id,
            name=row.name,
            content=version.content,
            version=version.version,
            sha256=version.sha256,
            size_bytes=version.size_bytes,
            excerpt=text_excerpt(version.content),
            updated_at=row.updated_at,
        )
