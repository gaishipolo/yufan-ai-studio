"""OSS 通道的**落地实现**：把素材字节上传到供应商签发的 OSS 临时目录，
拿回 `oss://` 直链。

**为什么单独成模块**：素材怎么送到上游，是「通道」的职责，不是某个驱动的
私事。此前 OSS 档位只在 `media_channel.py` 里做声明与校验，真正的落地却藏在
`drivers/bailian.py` 里 —— 换个供应商声明 OSS 通道，就得把直传代码再抄一遍，
而抄出来的第二份一定会走样。

**边界：这个模块不认任何供应商。** 供应商特有的部分只有「怎么签发上传
凭据」，那是驱动的事（`OssUploadTargetProvider` 协议）；本模块只认
「上传端点 + 表单字段 + 对象 key 前缀」这种协议中立的形态。

**为什么走表单直传**：供应商签发的是 OSS 表单直传凭据（`policy` /
`Signature` / `OSSAccessKeyId`），对象最终落在**供应商自己**的 OSS 临时目录
里，上游用 `oss://` 直链引用它 —— 我们不需要、也不该持有对象存储的长期
凭据。

**素材字节从我们的存储读**（`load_media_bytes`），不从归档工作区读：工作区
那份是物化时的快照，可能已经与存储里的对象不一致；真正要上传的东西，必须
是存储里**现在**的那份。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

import httpx

from infra.storage.base import StorageError

#: 表单直传的表单字段名（阿里云 OSS 表单直传的约定）。
_FILE_FIELD = "file"

#: 上传成功的状态码标记字段（表单直传把它放进响应体，用来确认落库成功）。
_SUCCESS_STATUS = "200"


@dataclass(frozen=True)
class OssUploadTarget:
    """供应商签发的 OSS 上传目标（协议中立，不含任何供应商语义）。

    - `upload_host`：直传端点，必须是 https；
    - `fields`：表单直传的固定字段（凭据、policy 等），**不含** `key` 与
      `success_action_status`（那两项由本模块按上传对象拼）；
    - `key_prefix`：对象 key 前缀（供应商签发的临时目录）。
    """

    upload_host: str
    fields: Mapping[str, str]
    key_prefix: str

    def object_key(self, filename: str) -> str:
        """按上传对象生成 OSS 里的对象 key。

        后缀沿用原文件名 —— 上游按后缀判类型（`.png` 与 `.mp4` 走不同的
        解析路径），所以后缀不能丢。
        """
        suffix = Path(filename).suffix
        prefix = self.key_prefix.rstrip("/")
        return f"{prefix}/{uuid.uuid4().hex}{suffix}"


@dataclass(frozen=True)
class OssUploadedMedia:
    """上传结果：`oss://` 直链 + 实际上传字节的摘要。

    带 `sha256` 是为了让调用方能核对「上传的字节」与「归档里记的那份」是不是
    同一份 —— 素材在物化之后被替换过，就该在提交期暴露，而不是让上游拿到
    一份和归档对不上的素材。
    """

    kind: str
    url: str
    sha256: str
    size: int


@runtime_checkable
class OssUploadTargetProvider(Protocol):
    """驱动能签发 OSS 上传目标。

    这是驱动在 OSS 通道里**唯一**该做的事：问供应商要一份直传凭据。
    拿到凭据之后怎么传、传成什么链接，都是通道层的事。
    """

    async def oss_upload_target(
        self,
        *,
        model: str,
        filename: str,
        client: httpx.AsyncClient | None = None,
    ) -> OssUploadTarget: ...


async def load_media_bytes(storage_key: str) -> bytes:
    """从我们的存储读素材字节。

    为什么不用归档工作区里那份：工作区是**物化时的快照**，物化之后存储里的
    对象若被替换（同一 key 覆盖上传），两份就不一样了。真正要送上游的必须是
    存储里现在这份。
    """
    from infra import get_storage

    if not storage_key:
        raise StorageError("参考素材缺少存储键")
    try:
        data = await get_storage().get(storage_key)
    except (StorageError, OSError):
        raise
    if not data:
        raise StorageError("参考素材字节已丢失")
    return data


async def upload_media_bytes(
    data: bytes,
    *,
    kind: str,
    filename: str,
    target: OssUploadTarget,
    timeout_sec: float,
    client: httpx.AsyncClient | None = None,
) -> OssUploadedMedia:
    """把素材字节直传到 OSS，返回 `oss://` 直链。

    `client` 可以由调用方传入（复用连接、便于测试注入）；不给就临时建一个。
    """
    host = target.upload_host
    if urlsplit(host).scheme != "https":
        raise ValueError("invalid upload host")

    key = target.object_key(filename)
    fields = {
        **dict(target.fields),
        "key": key,
        "success_action_status": _SUCCESS_STATUS,
    }

    if client is not None:
        response = await client.post(
            host,
            data=fields,
            files={_FILE_FIELD: (filename, data)},
        )
        response.raise_for_status()
    else:
        async with httpx.AsyncClient(timeout=timeout_sec, trust_env=False) as own:
            response = await own.post(
                host,
                data=fields,
                files={_FILE_FIELD: (filename, data)},
            )
            response.raise_for_status()

    return OssUploadedMedia(
        kind=kind,
        url=f"oss://{key}",
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
    )
