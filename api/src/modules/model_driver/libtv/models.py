"""LibTV 视频模型注册表与参数校验（与线上 `/api/tool_spec/list` 协议对齐）。

模型规格单一真相在同目录 `models.json`：Python 与 Node 桥接层（`runtime/bridge.mjs`）
共同读取它，避免两份配置漂移。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.exceptions import BadRequest

DEFAULT_LIBTV_MODEL_ID = "libtv-wan3.0"


@dataclass(frozen=True, slots=True)
class LibTVModelProfile:
    id: str
    label: str
    description: str
    upstream_model: str
    upstream_provider: str
    family: str
    resolutions: tuple[str, ...]
    default_resolution: str
    min_duration: int
    max_duration: int
    max_references: int
    image_max: int
    video_max: int
    audio_max: int
    total_max: int
    aliases: tuple[str, ...] = ()


def _load_profiles() -> tuple[LibTVModelProfile, ...]:
    raw_items: list[dict[str, Any]] = json.loads(
        Path(__file__).with_name("models.json").read_text(encoding="utf-8")
    )
    profiles: list[LibTVModelProfile] = []
    for item in raw_items:
        duration = item["duration"]
        mixed = item["mixed2video"]
        profiles.append(
            LibTVModelProfile(
                id=str(item["id"]),
                label=str(item["label"]),
                description=str(item["description"]),
                upstream_model=str(item["upstreamModel"]),
                upstream_provider=str(item["upstreamProvider"]),
                family=str(item["family"]),
                resolutions=tuple(str(r) for r in item["resolutions"]),
                default_resolution=str(item["defaultResolution"]),
                min_duration=int(duration["min"]),
                max_duration=int(duration["max"]),
                max_references=int(item["maxReferences"]),
                image_max=int(mixed["imageMax"]),
                video_max=int(mixed["videoMax"]),
                audio_max=int(mixed["audioMax"]),
                total_max=int(mixed["totalMax"]),
                aliases=tuple(str(a) for a in item.get("aliases", ())),
            )
        )
    return tuple(profiles)


LIBTV_PROFILES: tuple[LibTVModelProfile, ...] = _load_profiles()

_BY_KEY: dict[str, LibTVModelProfile] = {}
for _profile in LIBTV_PROFILES:
    for _key in (
        _profile.id,
        _profile.label,
        _profile.upstream_model,
        *_profile.aliases,
    ):
        _BY_KEY[_key] = _profile


def resolve_libtv_profile(model: str) -> LibTVModelProfile | None:
    """按目录 id、历史别名或上游模型名查找 LibTV 规格。"""
    return _BY_KEY.get((model or "").strip())


def resolve_libtv_resolution(profile: LibTVModelProfile, value: object) -> str:
    """按模型规格归一化分辨率大小写；缺省取模型默认档位。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return profile.default_resolution
    if not isinstance(value, str):
        raise BadRequest(f"模型 {profile.label} 的分辨率格式无效")
    needle = value.strip().lower()
    for allowed in profile.resolutions:
        if allowed.lower() == needle:
            return allowed
    raise BadRequest(
        f"模型 {profile.label} 不支持分辨率 {value.strip()!r}，"
        f"可选: {', '.join(profile.resolutions)}"
    )


def validate_libtv_references(profile: LibTVModelProfile, counts: Mapping[str, int]) -> None:
    """按上游 `mixed2video` 分类型与总量上限校验参考素材数量。"""
    images = int(counts.get("image", 0))
    videos = int(counts.get("video", 0))
    audios = int(counts.get("audio", 0))
    total = images + videos + audios
    if images > profile.image_max:
        raise BadRequest(
            f"模型 {profile.label} 最多支持 {profile.image_max} 张参考图（本次 {images} 张）"
        )
    if videos > profile.video_max:
        raise BadRequest(
            f"模型 {profile.label} 最多支持 {profile.video_max} 段参考视频（本次 {videos} 段）"
        )
    if audios > profile.audio_max:
        raise BadRequest(
            f"模型 {profile.label} 最多支持 {profile.audio_max} 段参考音频（本次 {audios} 段）"
        )
    if total > profile.total_max:
        raise BadRequest(
            f"模型 {profile.label} 参考素材总数不能超过 {profile.total_max} 个（本次 {total} 个）"
        )
