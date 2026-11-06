"""跨模块共用的媒体描述词汇；权限判断属于各自业务 service。"""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from core.pack_roles import PACK_ROLES, default_role
from core.schema import CamelModel, UtcDateTime

MediaCategory = Literal["source", "character", "product", "scene", "audio"]


class MediaFile(CamelModel):
    id: str
    name: str
    category: MediaCategory
    type: Literal["image", "video", "audio"]
    mime_type: str
    size_bytes: int
    path: str
    sha256: str
    created_at: UtcDateTime
    width: int = 0
    height: int = 0
    duration: float = 0
    has_audio: bool = False
    url: str
    preview_url: str | None = None


class StoredMedia(MediaFile):
    owner_id: str
    preview_path: str | None = None
    shared: bool = False
    shared_at: UtcDateTime | None = None
    deleted_at: UtcDateTime | None = None
    state_version: int = 0


class PackBinding(CamelModel):
    """一张参考图在封装里的用途；description 只补充本次任务特有的事实。"""

    role: str = ""
    description: str = ""


def aligned_bindings(kind: str, reference_ids: list[str], bindings: list) -> list[PackBinding]:
    """把用途对齐到参考图列表：非法或缺失的用途落到基准用途，多余的截掉。

    旧包只有 reference_ids、没有用途，读出来必须是「每张都能用」的状态；
    前端提交的 bindings 长度不一致时也走这里兜底，不让脏数据进存储。
    """
    allowed = PACK_ROLES.get(kind, {})
    base = default_role(kind)
    rows = [b if isinstance(b, PackBinding) else PackBinding.model_validate(b) for b in bindings]
    roles = [row.role if row.role in allowed else base for row in rows]
    roles = (roles + [base] * len(reference_ids))[: len(reference_ids)]
    return [
        PackBinding(
            role=role,
            description=rows[index].description if index < len(rows) else "",
        )
        for index, role in enumerate(roles)
    ]


def aligned_required_roles(kind: str, required: list[str]) -> list[str]:
    """必填用途永远包含基准用途，顺序按用途表固定。"""
    allowed = PACK_ROLES.get(kind, {})
    base = default_role(kind)
    picked = [role for role in required if role in allowed and role != base]
    return [base, *picked]


class MediaPack(CamelModel):
    """共享的发布记录格式；文件存储层不决定调用者的角色权限。"""

    id: str
    kind: Literal["character", "product", "scene"]
    name: str = ""
    reference_ids: list[str] = []
    cover_reference_id: str | None = None
    wardrobe_mode: Literal["preserve", "reference"] = "preserve"
    features: str = ""
    instructions: str = ""
    owner_id: str
    version: int = 0
    revision: int = 0
    status: Literal["draft", "published", "disabled"] = "draft"
    published_at: UtcDateTime | None = None
    # 逐图用途，与 reference_ids 同序；旧包没写这一项时按基准用途补齐，
    # 否则老数据一进来就过不了发布校验。
    bindings: list[PackBinding] = []
    # 缺少即不能发布的用途（基准用途另算，永远必填）
    required_roles: list[str] = []
    # 该包已审核的替换位置；产品替换只允许在其中选择
    supported_scopes: list[str] = []

    @model_validator(mode="after")
    def _normalize_bindings(self) -> MediaPack:
        self.bindings = aligned_bindings(self.kind, self.reference_ids, self.bindings)
        self.required_roles = aligned_required_roles(self.kind, self.required_roles)
        return self
