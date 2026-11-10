"""严格的本地配置预检；不联网、不写文件。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from core.exceptions import BadRequest
from core.schema import CamelModel
from modules.template.pack_roles import BASE_ROLE, PACK_ROLES, PACK_SCOPES
from modules.template.video_template_options import validate_options


class Config(CamelModel):
    model_config = ConfigDict(extra="forbid")


class Video(Config):
    file: Path
    shared: bool = False


class Reference(Config):
    key: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    file: Path
    role: str
    description: str = Field(default="", max_length=2000)


class Pack(Config):
    key: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    name: str = Field(min_length=1, max_length=160)
    kind: Literal["product", "character", "scene"]
    references: list[Reference] = Field(min_length=1, max_length=10)
    required_roles: list[str] = Field(default_factory=list, max_length=8)
    supported_scopes: list[str] = Field(default_factory=list, max_length=8)
    wardrobe_mode: Literal["preserve", "reference"] = "preserve"

    @model_validator(mode="after")
    def check_references(self):
        keys = [ref.key for ref in self.references]
        roles = {ref.role for ref in self.references}
        if len(set(keys)) != len(keys):
            raise ValueError("参考图 key 不能重复")
        if not roles <= PACK_ROLES[self.kind].keys():
            raise ValueError("图片用途不属于当前素材包类型")
        if not ({BASE_ROLE[self.kind]} | set(self.required_roles)) <= roles:
            raise ValueError("参考图缺少必备用途")
        if not set(self.supported_scopes) <= PACK_SCOPES.keys():
            raise ValueError("素材包支持范围无效")
        if self.kind == "product" and not self.supported_scopes:
            raise ValueError("产品素材包必须声明 supportedScopes")
        return self


class Function(Config):
    key: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    name: str = Field(min_length=1, max_length=80)
    pack: str
    prompt: str | None = Field(default=None, min_length=1, max_length=20000)
    prompt_file: Path | None = None
    media_order: list[str] = Field(default_factory=list, max_length=10)
    scope_options: list[str] = Field(default_factory=list, max_length=30)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    duration: int | None = Field(default=None, ge=2, le=15)


class Manifest(Config):
    key: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    name: str = Field(min_length=1, max_length=100)
    video: Video
    packs: list[Pack] = Field(min_length=1, max_length=20)
    functions: list[Function] = Field(min_length=1, max_length=30)


def digest_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


@dataclass
class LoadedManifest:
    spec: Manifest
    fingerprint: str
    digests: dict[Path, str]


def load_manifest(path: Path) -> LoadedManifest:
    path = path.resolve()
    spec = Manifest.model_validate_json(path.read_text(encoding="utf-8-sig"))
    digests: dict[Path, str] = {}

    def media(file: Path, extensions: set[str]) -> Path:
        resolved = (path.parent / file).resolve()
        if not resolved.is_file() or resolved.suffix.lower() not in extensions:
            raise ValueError(f"文件不存在或类型不支持：{resolved}")
        if not resolved.stat().st_size:
            raise ValueError(f"文件为空：{resolved}")
        digests[resolved] = digest_file(resolved)
        return resolved

    spec.video.file = media(spec.video.file, {".mp4"})
    if spec.video.file.stat().st_size > 300 * 1024 * 1024:
        raise ValueError("视频最大 300MB")
    packs = {pack.key: pack for pack in spec.packs}
    if len(packs) != len(spec.packs) or len({f.key for f in spec.functions}) != len(spec.functions):
        raise ValueError("素材包或功能 key 不能重复")
    for pack in spec.packs:
        for ref in pack.references:
            ref.file = media(ref.file, {".png", ".jpg", ".jpeg", ".webp"})
        if len({digests[ref.file] for ref in pack.references}) != len(pack.references):
            raise ValueError("同一素材包不能重复使用内容相同的图片")
    for function in spec.functions:
        if (
            not spec.name.strip()
            or not function.name.strip()
            or len(f"{spec.name} · {function.name}") > 160
        ):
            raise ValueError("卡片名称与功能名称组合后不能为空且不能超过160字")
        if function.pack not in packs:
            raise ValueError(f"功能引用的素材包不存在：{function.pack}")
        if (function.prompt is None) == (function.prompt_file is None):
            raise ValueError("prompt 与 promptFile 必须且只能提供一项")
        if function.prompt_file is not None:
            function.prompt = (path.parent / function.prompt_file).read_text(encoding="utf-8-sig")
            function.prompt_file = None
        prompt = function.prompt or ""
        if not prompt.strip() or len(prompt) > 20000:
            raise ValueError("提示词不能为空且不能超过20000字")
        try:
            validate_options(
                prompt, {"workbenchLabel": function.name, "scopeOptions": function.scope_options}
            )
        except BadRequest as exc:
            raise ValueError(exc.message) from exc
        pack = packs[function.pack]
        keys = [ref.key for ref in pack.references]
        if function.media_order and sorted(function.media_order) != sorted(keys):
            raise ValueError("mediaOrder 必须完整且不重复地列出当前包的参考图 key")
        function.media_order = function.media_order or keys
        tokens = re.findall(r"\{\{([^{}]+)\}\}", prompt)
        numbers = []
        for token in tokens:
            if token == "ReplacementScope":
                continue
            if not re.fullmatch(r"Mixed [1-9]\d*", token):
                raise ValueError(f"非法提示词引用：{token}")
            numbers.append(int(token[6:]))
        if 1 not in numbers or not any(n >= 2 for n in numbers):
            raise ValueError("提示词必须引用原片 {{Mixed 1}} 及至少一张参考图")
        if max(numbers) > len(keys) + 1:
            raise ValueError("提示词 Mixed 编号超出素材数量")
    # 内容摘要不依赖机器路径，移动整个素材文件夹后仍可恢复同一份配置。
    data = spec.model_dump(mode="json", by_alias=True)
    data["video"]["file"] = digests[spec.video.file]
    for pack, row in zip(spec.packs, data["packs"], strict=True):
        for ref, item in zip(pack.references, row["references"], strict=True):
            item["file"] = digests[ref.file]
    fingerprint = hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return LoadedManifest(spec, fingerprint, digests)
