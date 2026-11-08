"""封装素材的角色词汇：图片用途、必备角色与可替换范围。

这些常量决定三件事，改动前先确认三处都还能对上：
1. 封装编辑器给每张参考图选什么用途（前端 `entities/template/model/packRoles.ts`
   是它的副本，`tests/test_pack_roles_sync.py` 逐字比对，漂移就红）；
2. 发布前校验哪些用途不能缺（`required_roles` ∪ 基准角色）；
3. 生成提示词时每张图被赋予的说明文字（`usage`）。

**位置变更记录**：这份词表原先在 `core/pack_roles.py`。搬进模块的判据是
「放在 core 的条件是两个以上互不认识的模块都要用」——它只有一个消费者
（模板库），另一个引用它的 `core/media.py` 本身也是模板私有代码。放在 core
只会让「删掉模板库」变成一次跨层手术。
"""

from __future__ import annotations

from typing import Literal

PackKind = Literal["character", "product", "scene"]

# 用途 key -> 中文名。顺序即编辑器里的展示顺序，也是拼装提示词时的稳定顺序。
PACK_ROLES: dict[str, dict[str, str]] = {
    "product": {
        "front": "产品主图",
        "multiview": "多视结构图",
        "detail": "产品细节图",
        "back": "背面结构图",
        "side": "侧面立体图",
    },
    "character": {
        "front": "正面身份图",
        "side": "侧脸身份图",
        "fullbody": "全身体态图",
    },
    "scene": {
        "main": "环境主参考",
        "angle": "同空间补充视角",
        "detail": "环境材质细节",
    },
}

# 每个类型必须有的用途：缺了它，替换目标就没有稳定外观可依。
BASE_ROLE: dict[str, str] = {"product": "front", "character": "front", "scene": "main"}

# 产品替换位置。素材包只声明自己审核过的范围，运营不能越过它。
PACK_SCOPES: dict[str, str] = {
    "worn": "人物上身穿着",
    "held": "手持展示产品",
    "boxed": "盒内可见产品",
    "screen": "手机详情页产品图",
}

# 进入提示词的逐图说明：说清“这张图能决定什么、不能决定什么”，
# 避免多视图被当成多个产品、细节图被当成整体尺寸。
ROLE_USAGE: dict[str, dict[str, str]] = {
    "product": {
        "front": (
            "以产品主图确定同款同色目标产品的完整外观、颜色和花纹；"
            "反光、曝光差异不代表另一颜色"
        ),
        "multiview": (
            "这是同一产品的多视拼图；正面、侧面和背面共同提供结构、轮廓、肩带与后扣信息，"
            "不是多个产品，不把拼图布局复制进视频"
        ),
        "detail": (
            "只补充该产品局部花纹、材质、内里、缝线和扣件；"
            "若此图含整体视图，仅用来辅助主图，不把细节特写当整体尺寸或复制拼图布局"
        ),
        "back": "只补充该产品背面、背带和后扣结构",
        "side": "只补充该产品侧翼、厚度和立体轮廓",
    },
    "character": {
        "front": "确定同一个目标人物的正面五官和稳定身份",
        "side": "补充同一人的侧脸轮廓，不引入另一身份",
        "fullbody": "补充同一人的身体比例，服装采用范围以本任务约定为准",
    },
    "scene": {
        "main": "确定目标空间的整体布局、建筑与光线，不采用图中人物或商品",
        "angle": "补充同一空间另一视角的透视和陈设，不新增镜头或动作",
        "detail": "只补充目标环境的材质和陈设细节",
    },
}


def role_label(kind: str, role: str) -> str:
    return PACK_ROLES.get(kind, {}).get(role, role)


def roles_for(kind: str) -> dict[str, str]:
    return PACK_ROLES.get(kind, {})


def default_role(kind: str) -> str:
    return BASE_ROLE.get(kind, "front")


def scope_label(scope: str) -> str:
    return PACK_SCOPES.get(scope, scope)


def usage_for(kind: str, role: str) -> str:
    return ROLE_USAGE.get(kind, {}).get(role, "")


def ordered_roles(kind: str, roles: list[str]) -> list[str]:
    """按用途表的固定顺序整理，保证同样的包每次拼出同样的提示词。"""
    order = list(roles_for(kind))
    return sorted(roles, key=lambda role: order.index(role) if role in order else len(order))


def missing_role_labels(kind: str, present: list[str], required: list[str]) -> list[str]:
    labels = roles_for(kind)
    return [labels[role] for role in labels if role in required and role not in present]
