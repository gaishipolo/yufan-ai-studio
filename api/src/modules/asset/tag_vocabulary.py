"""标签初始词表（B1 定稿）：6 个维度、46 个词。

**这是数据，不是规则。** 写死在代码里而不是塞进迁移，理由有三：

1. 它是产品决定（「新建一个库时该有哪几个词」），改它应当是代码评审，
   而不是后台点一下——和 `library_service.PURPOSES` 同一个取舍；
2. 迁移里写 `INSERT` 之后，`app seed --reset` 会按 metadata 清表把它清掉，
   于是「演示库有词表、生产库没有」这种只在一种环境复现的差异就出现了；
3. 幂等初始化（`ensure_vocabulary`）能让它随时补齐，包括给新建的库补。

## 为什么维度名是英文码、词是中文

维度名要进 URL 与筛选参数（`?dimension=style`），中文做参数会在每次编码/解码
里留一个坑；而词是给人看的，翻译成英文只会让「国潮国风」这类词失去含义。
所以：**机器读维度，人读词**。

## 词表为什么不能自由填

判据在 `ASSET_LIBRARY_REDESIGN.md` §3.3：能不能让两个人在 3 秒内对同一张图
给出相同答案。「高级感」不行，「极简留白」行。自由填写三个月后必然同时出现
「正面图」「正面」「正视图」三种写法，而那正是这次改造要解决的问题。
"""

from __future__ import annotations

from typing import Final

__all__ = ["DIMENSIONS", "DIMENSION_LABELS", "INITIAL_TAGS", "TAG_TOTAL"]


#: 维度顺序即筛选条的分组顺序。字典保持插入序，别改成 `set`。
DIMENSIONS: Final[dict[str, tuple[str, ...]]] = {
    "style": (
        "写实摄影",
        "电影感",
        "广告质感",
        "3D 渲染",
        "插画手绘",
        "扁平矢量",
        "胶片颗粒",
        "极简留白",
        "国潮国风",
        "赛博霓虹",
    ),
    "scene": (
        "棚拍纯色",
        "室内家居",
        "办公空间",
        "城市街景",
        "自然户外",
        "餐厨空间",
        "工业车间",
        "抽象背景",
        "合成虚拟",
    ),
    "composition": ("无人", "单人", "多人", "手部特写", "宠物", "群体"),
    "shot": ("特写", "中景", "全景", "俯拍", "平视", "推拉", "环绕", "手持"),
    "placement": ("主图", "详情页", "封面", "开屏", "信息流", "贴片", "直播"),
    "content": ("含真人", "含未成年人", "含品牌标识", "需换脸", "需打码", "无水印"),
}

#: 维度码 → 中文名。筛选条的分组标题用它，不用前端硬编码（改名只改一处）。
DIMENSION_LABELS: Final[dict[str, str]] = {
    "style": "风格",
    "scene": "场景",
    "composition": "主体构成",
    "shot": "镜头",
    "placement": "用途/版位",
    "content": "内容属性",
}

#: 展平成 `(维度, 词)` 序列，供幂等初始化遍历。
INITIAL_TAGS: Final[tuple[tuple[str, str], ...]] = tuple(
    (dimension, name) for dimension, names in DIMENSIONS.items() for name in names
)

#: 46。写成断言常量而不是注释里的数字：改词表时 `tests/test_asset_tags.py`
#: 会跟着这条一起红，逼人回来确认「数量变了是有意的吗」。
TAG_TOTAL: Final[int] = len(INITIAL_TAGS)
