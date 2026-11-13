"""manifest 配置的 HTTP 契约。

## 与原 `template_packager/manifest.py` 的对应关系

字段逐项对齐，**只少了与本地文件有关的三个**：`video.file`、
`references[].file`、`functions[].promptFile`。素材一律是 `library_assets.id`，
提示词直接存正文（DB 里没有「另一个文件」）。

严格模式（`extra="forbid"`）保留：CLI 时期拼错一个键会静默被忽略、直到发布
出的卡片少一条约束才被发现；DB 化后同样要靠入参当场拒绝。
校验规则本身在 `manifest_service.validate()`，这里只管形状。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field

from core.schema import CamelModel

#: 卡片名称组合上限、提示词上限等与原 manifest 保持同一口径。
MAX_PACKS = 20
MAX_FUNCTIONS = 30
MAX_REFERENCES = 10
MAX_PROMPT = 20000

PackKind = Literal["product", "character", "scene"]
AspectRatio = Literal["9:16", "16:9", "1:1"]
WardrobeMode = Literal["preserve", "reference"]


class StrictIn(CamelModel):
    """所有入参都拒绝未声明的键。"""

    model_config = ConfigDict(extra="forbid")


class ManifestVideoIn(StrictIn):
    asset_id: str = Field(min_length=1, max_length=64)
    #: 原 `video.shared`：素材已在资产库里，这里只表达「发布时要求原片可见范围」。
    shared: bool = False


class ManifestReferenceIn(StrictIn):
    key: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    asset_id: str = Field(min_length=1, max_length=64)
    role: str = Field(min_length=1, max_length=24)
    description: str = Field(default="", max_length=2000)


class ManifestPackIn(StrictIn):
    key: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    name: str = Field(default="", max_length=160)
    kind: PackKind
    #: 非空 = 复用已发布的素材包；此时 `references` 必须为空。
    pack_id: str | None = Field(default=None, min_length=1, max_length=64)
    references: list[ManifestReferenceIn] = Field(
        default_factory=list, max_length=MAX_REFERENCES
    )
    required_roles: list[str] = Field(default_factory=list, max_length=8)
    supported_scopes: list[str] = Field(default_factory=list, max_length=8)
    wardrobe_mode: WardrobeMode = "preserve"


class ManifestFunctionIn(StrictIn):
    key: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    name: str = Field(min_length=1, max_length=80)
    pack: str | None = None
    kind: PackKind | None = None
    scene_prompt_default: str | None = None
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT)
    media_order: list[str] = Field(default_factory=list, max_length=MAX_REFERENCES)
    bindings: dict[str, str] | None = None
    scope_options: list[str] = Field(default_factory=list, max_length=30)
    aspect_ratio: AspectRatio = "9:16"
    duration: int | None = Field(default=None, ge=2, le=15)


class ManifestDraftIn(StrictIn):
    key: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    video: ManifestVideoIn
    packs: list[ManifestPackIn] = Field(default_factory=list, max_length=MAX_PACKS)
    functions: list[ManifestFunctionIn] = Field(min_length=1, max_length=MAX_FUNCTIONS)


class ManifestOut(CamelModel):
    """配置详情。嵌套体直接复用入参模型：形状一致，不另造一份会漂移的出参。"""

    id: str
    key: str
    name: str
    description: str
    owner_id: str
    video: ManifestVideoIn
    status: str
    version: int
    revision: int
    fingerprint: str
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    packs: list[ManifestPackIn] = Field(default_factory=list)
    functions: list[ManifestFunctionIn] = Field(default_factory=list)


class ManifestListOut(CamelModel):
    items: list[ManifestOut] = Field(default_factory=list)
    total: int = 0


class ManifestValidateOut(CamelModel):
    """只预检不落库：返回会用于幂等判定的配置指纹。"""

    valid: bool = True
    fingerprint: str


class PublishedTemplateOut(CamelModel):
    function_key: str
    template_id: str
    template_key: str
    name: str
    workbench_path: str


class ManifestPublishOut(CamelModel):
    """发布回执：`templateId` 即工作台卡片 id，前端拼 `workbenchPath` 用。"""

    manifest: ManifestOut
    version: int
    templates: list[PublishedTemplateOut] = Field(default_factory=list)
