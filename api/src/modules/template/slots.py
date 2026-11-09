"""槽位契约：模板「需要哪些角色、几张图、哪些能力」。

## 为什么是纯函数 + 冻结 dataclass

槽位声明错了，症状不是报错，而是**取不到图**：模板按 `required_roles` 去主体里
挑成员，挑不到就生成一张缺角色的成片，用户看到的是"效果不对"而不是"配置错了"。
所以校验要在**写入时**就做完，而不是等到试跑。

纯函数让这件事可以穷举：角色不在词表、`min>max`、`generate` 缺失、封面角色
不在声明里——各一条用例，十行一个，不用造库。

## `generate` 是硬性项

模板要生成，就必须能读全部成员字节。允许声明一个不含 `generate` 的槽位，
等于允许配出一个"永远跑不起来"的模板，而失败会出现在生成流水线里
（那里只报"参考图读取失败"）。因此 `generate` 不在 `required_caps` 里直接拒绝。

`download` 只在"要取原片另存"的模板里出现：它决定成片能不能被下载，
不是能不能生成。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from core.asset_capabilities import CAPABILITIES
from modules.template.pack_roles import BASE_ROLE, PACK_ROLES

__all__ = [
    "MAX_IMAGES_LIMIT",
    "Slots",
    "SlotError",
]

#: 单个模板最多声明几张成员图。上限存在的原因是提示词编译：每张图都要在
#: 提示词里占一段说明，超过 12 张时模型已经分不清 reference-3 和 reference-9。
MAX_IMAGES_LIMIT: Final[int] = 12

#: 什么都不声明时的默认值。`required_roles` 只放该类型的基础角色（产品=正面图），
#: 因为它是"缺了就没有稳定外观可依"的那一个，别的都可以后补。
DEFAULT_MIN_IMAGES: Final[int] = 1


class SlotError(ValueError):
    """槽位声明不合法。消息面向用户（会原样出现在 400 里）。"""


@dataclass(frozen=True, slots=True)
class Slots:
    """一个模板的槽位声明。不可变：改槽位等于发新版本。"""

    required_roles: tuple[str, ...]
    optional_roles: tuple[str, ...]
    min_images: int
    max_images: int
    required_caps: tuple[str, ...]
    preferred_cover_role: str

    @classmethod
    def default(cls, kind: str) -> Slots:
        """按类型给一份最小可用声明：基础角色必需、能生成、1–4 张。"""
        base = BASE_ROLE.get(kind)
        if base is None:
            raise SlotError(f"不认识的模板类型：{kind}")
        return cls(
            required_roles=(base,),
            optional_roles=(),
            min_images=DEFAULT_MIN_IMAGES,
            max_images=4,
            required_caps=("generate",),
            preferred_cover_role=base,
        )

    @classmethod
    def parse(cls, raw: dict[str, Any] | None, kind: str) -> Slots:
        """从 JSON 解析并校验。空对象回落到 `default(kind)`。

        **不接受未知键**：多写一个 `required_role`（少个 s）如果不报错，
        槽位就静默变成"什么都不需要"，而这个模板会一直跑出缺角色的图。
        """
        if not raw:
            return cls.default(kind)
        vocabulary = PACK_ROLES.get(kind)
        if vocabulary is None:
            raise SlotError(f"不认识的模板类型：{kind}")

        allowed = {
            "required_roles",
            "optional_roles",
            "min_images",
            "max_images",
            "required_caps",
            "preferred_cover_role",
        }
        unknown = set(raw) - allowed
        if unknown:
            raise SlotError(f"槽位里有不认识的字段：{'、'.join(sorted(unknown))}")

        required = _roles(raw.get("required_roles"), vocabulary, "必填角色")
        optional = _roles(raw.get("optional_roles"), vocabulary, "可选角色")
        overlap = set(required) & set(optional)
        if overlap:
            # 同一个角色不能既必填又可选：那等于"缺了也行"，与必填自相矛盾
            raise SlotError(f"角色不能同时是必填和可选：{'、'.join(sorted(overlap))}")
        if not required:
            raise SlotError("至少要声明一个必填角色，否则模板不知道该按什么取图")

        min_images = _int(raw.get("min_images"), DEFAULT_MIN_IMAGES, "最少张数")
        max_images = _int(raw.get("max_images"), 4, "最多张数")
        if min_images < 1:
            raise SlotError("最少张数不能小于 1")
        if max_images < min_images:
            raise SlotError(f"最多张数不能小于最少张数（{min_images}）")
        if max_images > MAX_IMAGES_LIMIT:
            raise SlotError(f"最多张数上限是 {MAX_IMAGES_LIMIT}")

        caps = _caps(raw.get("required_caps"))
        cover = str(raw.get("preferred_cover_role") or "").strip()
        if cover and cover not in set(required) | set(optional):
            raise SlotError(f"封面角色 {cover} 不在声明的角色里")

        return cls(
            required_roles=required,
            optional_roles=optional,
            min_images=min_images,
            max_images=max_images,
            required_caps=caps,
            preferred_cover_role=cover or (required[0] if required else ""),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "required_roles": list(self.required_roles),
            "optional_roles": list(self.optional_roles),
            "min_images": self.min_images,
            "max_images": self.max_images,
            "required_caps": list(self.required_caps),
            "preferred_cover_role": self.preferred_cover_role,
        }


def _roles(raw: Any, vocabulary: dict[str, str], label: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise SlotError(f"{label}必须是数组")
    # 去重但保序：顺序决定提示词里 reference-N 的编号，乱序会让同一份配置
    # 每次编出不同的提示词——用户会以为模型不稳定
    seen: list[str] = []
    for item in raw:
        key = str(item).strip()
        if not key:
            continue
        if key not in vocabulary:
            raise SlotError(f"{label}里的 {key} 不在该类型的角色词表里")
        if key not in seen:
            seen.append(key)
    return tuple(seen)


def _int(raw: Any, fallback: int, label: str) -> int:
    if raw is None:
        return fallback
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise SlotError(f"{label}必须是整数") from exc
    return value


def _caps(raw: Any) -> tuple[str, ...]:
    if raw is None:
        raw = ["generate"]
    if not isinstance(raw, list):
        raise SlotError("能力要求必须是数组")
    caps: list[str] = []
    for item in raw:
        key = str(item).strip()
        if not key:
            continue
        if key not in CAPABILITIES:
            raise SlotError(f"能力 {key} 不在 {'/'.join(CAPABILITIES)} 里")
        if key not in caps:
            caps.append(key)
    if "generate" not in caps:
        # 没有生成能力 = 永远跑不起来的模板，失败会出现在生成流水线里而不是这里
        raise SlotError("槽位必须包含 generate：模板要读全部成员字节才能生成")
    return tuple(caps)
