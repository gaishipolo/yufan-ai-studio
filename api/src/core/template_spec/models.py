"""模板规格的数据形状：槽位、规格、填充结果、编译产物。

全部是 `frozen` dataclass：规格一旦发布就是历史，任何"就地改一下"都会让
同一条提示词在不同任务里算出不同编号。

**这里刻意没有"编号"字段**。`{{Mixed N}}` 是 `compile` 按槽位顺序与实际填充
数量算出来的（见 `compile.py`），不落库、不进模型——用户看到的是缩略图，
不是数字。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

#: 槽位类型。**就是资产库的 kind**（图片 / 视频 / 音频 / 文本）。
#: `text` 与其余的区别：正文注入，不占视觉编号。
SlotType = Literal["image", "video", "audio", "text"]
#: 谁定内容。`fixed` = 封装时定死，模板库只读；`user` = 模板库填。
SlotBinding = Literal["fixed", "user"]
#: `mixed` 参与 `{{Mixed N}}` 编号；`attachment` 作为附加输入、不占号。
RefMode = Literal["mixed", "attachment"]

SLOT_TYPES: tuple[SlotType, ...] = ("image", "video", "audio", "text")
#: 封装容器里能放的类型：只有图片/视频参与 `{{Mixed N}}` 分发，文本与音频
#: 各有各的选择器，包里没有它们的位置。
PACK_GROUP_TYPES: tuple[SlotType, ...] = ("image", "video")

#: `mixed` 槽位展开后的总张数上限。执行器侧的参考素材上限，超了必然 400，
#: 不如在编译期就说清。
MAX_SLOT_REFS = 10
#: 单个版本最多几个槽位。防"把整本画册塞进一个模板"。
MAX_SLOTS = 20
#: 一个槽位最多能圈几个标签（候选集范围）。标签树只有三档，但"产品 + 角色 +
#: 某个具体词"这种组合是真实需求 —— 给个上限免得有人把整本词表塞进来。
MAX_SLOT_TAGS = 20
#: **编写**上限（两段合计），沿用旧 manifest 的口径。草稿可以这么长。
MAX_PROMPT = 20000
#: 一份模板最多声明几个「允许选用的模型」。这是**给人挑的清单**，不是模型目录的
#: 镜像：列几十个等于没列（使用者还是不知道该挑哪个），也把「这份模板适用于哪些
#: 模型」这件事摊成了流水账。十来个已经是「挑得动」的上限。
MAX_ALLOWED_MODELS = 10
#: **执行**上限：编译产物要交给 `POST /tasks`，而它的 `CreateTaskIn.prompt` 是
#: `max_length=7000`。超过它在填表页点「生成」才会 422 —— 那时用户已经填完素材，
#: 错却出在提示词长度上，很不好解释。所以在发布（粗判）与 `compile`（按展开后精确判）
#: 两处提前挡。
#:
#: 这个数是**执行端点**的属性，不是模板的属性：替换链吃 20000 级别的提示词（P3 之前的
#: 旧模板就是这样）。将来第二个执行端点进来时，它应该随该能力声明，而不是改这里。
#: 漂移由 `tests/test_template_spec_api.py::test_execution_prompt_limit_matches_task_schema` 钉住。
EXECUTION_PROMPT_LIMIT = 7000


@dataclass(frozen=True, slots=True)
class InputSlot:
    """一个输入槽位。

    `type` 就是**资产库的 kind**：`image` / `video` / `audio` / `text`。

    `type` 与 `binding` 是**两个正交的轴**，组合都合法（各自的约束见 `rules.py`）：

    | | `binding=fixed`（封装时定） | `binding=user`（模板库填） |
    |---|---|---|
    | `type=image/video` | 绑定某一具体素材 | 模板库挑（`min/max` 可多值） |
    | `type=text/audio` | 绑定一份文案/一段音频 | 模板库挑（附加输入，不占号） |

    `group` 是**素材从哪来**这根轴，而它只有一种表达方式：

    - `group` 为空（独立槽位）→ 从候选**素材**里挑；
    - `group` 相同（封装容器）→ 这一组共用一次「选包」，只能从**封装包**取。

    所以没有「来源」这个字段：来源是位置的结果，不是作者要单独声明的东西。
    （2026-11-20 删掉了 `pick_mode` —— 它和 `group` 表达同一件事，两处并存时
    会出现「在容器里、却声明从素材取」这种没法解释的组合。）

    `tag_ids` 是**候选集的范围**，不是内容：非空时固定/填写都只在这几个标签
    （各自及其下级）里挑。它只约束"能选什么"，编译期不看它。

    标签**可以是任意级别**（一级分组 / 二级系列 / 三级具体词）：槽位的标签是
    "圈定候选范围"，不是"打在素材上"，所以不必限叶子 —— 圈一个分组正是它的
    用途。多个标签之间是并集：任一标签命中即在候选集内。
    """

    key: str
    type: SlotType
    binding: SlotBinding = "user"
    ref_mode: RefMode = "mixed"
    #: 所属的**封装容器**（key；空 = 独立槽位）。
    #:
    #: 同一容器的几个槽位共用一个「选包」入口：使用者点一次包，按标签把成员
    #: 分到各槽位 —— 「几个插槽的素材来自同一个包」就是它。容器里的槽位
    #: 只能是**用户填的图片/视频**，且顺序上必须**相邻**（见 `rules._validate_groups`）。
    group: str = ""
    #: 只有 `binding=user` 时才有意义：固定槽位恒定一个素材。
    #: **`min_count=0` 就是「可选」** —— 不填素材也能编译（不占编号、不进参考素材）。
    min_count: int = 1
    max_count: int = 1
    label: str = ""
    asset_id: str | None = None
    #: **选中时文案**：使用者填了素材时，提示词里这一段怎么说。
    #:
    #: 为什么它住在槽位里而不写在提示词正文：正文中 `{{slot:key}}` 是**唯一插入点**，
    #: 这一段的具体措辞跟着槽位走 —— 「有素材时说 A，没素材时说 B」是同一个槽位的
    #: 两种说法，写在正文里就得让作者把同一句话维护两遍（正文一遍、槽位一遍），
    #: 两处措辞一漂移，编译时就对不上。
    #:
    #: 为空 = 保持旧行为：直接插入 `{{Mixed N}}`（或 `{{Text N}}`）。
    #: 可含 `{{slot:key}}` **自引用**（派生成编号）—— 典型写法
    #: 「人物参考 {{slot:hero}}」。
    filled_text: str = ""
    #: **未选时文案**：使用者**没填**素材时，提示词里这一段怎么说。
    #:
    #: 只对**可选槽位**（`min_count == 0`）有意义：必填槽位不填会在编译期直接
    #: 报错，用不上这段文案。
    #:
    #: 纯提示词，**不许含槽位引用**（没素材就没有编号可指）。为空 = 编译期报错
    #: 并说清「给个兜底文案」—— 静默留空会让提示词悄悄缺一段，模型照跑、
    #: 结果不对且不留痕迹。
    empty_text: str = ""
    #: 候选集的标签 id（资产库 `library_tags`，任意级别）。空 = 不限；非空 = 只在
    #: 这几个标签（各自及其下级）里挑。**候选集范围，不是内容**：编译不看它，
    #: 删了标签也不影响已发布版本。
    tag_ids: tuple[str, ...] = ()
    #: 排序，**就是编号顺序**。编号本身不落库，由它算出来。
    position: int = 0


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    """一份模板规格（发布版本的快照形状，也是草稿的形状）。"""

    key: str
    name: str
    model: str
    slots: tuple[InputSlot, ...]
    #: 整段提示词。**不分固定段/可编辑段**：模板库不改文字，需要可变的地方
    #: 用 `text` 槽位（填表时选一份文案，编译成 `{{Text N}}` 由执行层注入正文）。
    prompt: str
    #: **随机档提示词**：作者给「随机」档另写的一整段提示词，随机档时**整段替换**
    #: `prompt`（不是追加）。空 = 该模板不支持随机档，前端据此把这一档置灰。
    #:
    #: 为什么不按槽位拆碎：随机要变的是「整个场景怎么写」，不是某一句兜底文案。
    #: 按槽位拆会让作者在十几个格子里各写几条，而真实变化往往是整段重写。
    #:
    #: 引用规则与 `prompt` 完全相同（可引用任意槽位、不许裸写 `{{Text N}}`）；
    #: 随机档不传可选素材，所以它引用可选槽位时同样要求该槽位有 `empty_text`。
    random_prompt: str = ""
    #: 使用模板时**允许选用的模型清单**。空 = 只有 `model` 那一个（老模板的行为）。
    #:
    #: 为什么不是「一个固定模型就够了」：作者比使用者更清楚这份模板适用于哪几款
    #: 模型（同一段提示词在视频模型 A 与 B 上表现差别很大）。写死一个模型等于替
    #: 使用者做了决定，而使用者恰恰是知道自己在哪个渠道发布的人。
    #:
    #: 清单里的模型 id 必须**存在于模型目录**（发布时校验，见
    #: `spec_service._check_model`）—— 清单是给人看的选项，里面出现一个不存在
    #: 的模型，使用者挑到它才 400，那时素材都填完了。
    allowed_models: tuple[str, ...] = ()
    #: 传给执行层的参数默认值。键必须在该模型的 `param_schema` 里（发布时校验）。
    params: Mapping[str, Any] = field(default_factory=dict)
    description: str = ""
    #: 收编替换链时承载 `scope` / `wardrobe` 这类非核心开关，避免每来一个概念加一列。
    attributes: Mapping[str, str] = field(default_factory=dict)
    #: 展示案例（图片或视频的资产 id）。**纯展示**：不进发布快照、不参与编译，
    #: 改案例不该要求发新版本。模板库卡片与详情用它给用户看"长什么样"。
    demo_asset_id: str | None = None


@dataclass(frozen=True, slots=True)
class FilledAsset:
    """一个填进槽位的素材。`kind` 来自资产库，用于类型校验（不信调用方口头声明）。"""

    asset_id: str
    kind: str
    role: str = ""
    name: str = ""


@dataclass(frozen=True, slots=True)
class FilledSlot:
    """某个槽位这次被填成什么。

    `assets` **必须已经是最终顺序**：挑选顺序就是编号顺序，编译不得重排
    （重排就等于模板偷偷定了一份与用户所见不同的顺序）。
    """

    slot_key: str
    assets: tuple[FilledAsset, ...] = ()


@dataclass(frozen=True, slots=True)
class CompiledRequest:
    """编译产物：**模板层**结果（提示词 + 素材序 + 模型 + 参数）。

    它还不是任何端点的请求体：`POST /tasks` 把时长/画幅放在顶层、不接受自由
    `params`，生图端点又是另一套摆法。**摊平由提交方做**（模板库页面对照生成的
    `CreateTaskIn` 类型摊 `params`，类型检查即守卫）——compile 完全不认识执行层，
    否则换一个执行端点就要改编译规则。

    `slot_tokens` 只给前端画角标（哪张图是 `{{Mixed 3}}`），不参与提交。
    """

    model: str
    prompt: str
    params: Mapping[str, Any]
    reference_ids: tuple[str, ...]
    text_ids: tuple[str, ...]
    slot_tokens: Mapping[str, tuple[str, ...]]
