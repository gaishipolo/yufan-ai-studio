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

#: 槽位类型。`text` 与另外三种的区别：正文注入，不占视觉编号。
SlotType = Literal["image", "video", "audio", "text"]
#: 指向单个资产，还是资产库封装（多成员、带角色与顺序）。
SlotSource = Literal["asset", "pack"]
#: 谁定内容。`fixed` = 封装时定死，模板库只读；`user` = 模板库填。
SlotBinding = Literal["fixed", "user"]
#: `mixed` 参与 `{{Mixed N}}` 编号；`attachment` 作为附加输入、不占号。
RefMode = Literal["mixed", "attachment"]

SLOT_TYPES: tuple[SlotType, ...] = ("image", "video", "audio", "text")

#: `mixed` 槽位展开后的总张数上限。执行器侧的参考素材上限，超了必然 400，
#: 不如在编译期就说清。
MAX_SLOT_REFS = 10
#: 单个版本最多几个槽位。防"把整本画册塞进一个模板"。
MAX_SLOTS = 20
#: **编写**上限（两段合计），沿用旧 manifest 的口径。草稿可以这么长。
MAX_PROMPT = 20000
#: **执行**上限：编译产物要交给 `POST /tasks`，而它的 `CreateTaskIn.prompt` 是
#: `max_length=2000`。超过它在填表页点「生成」才会 422 —— 那时用户已经填完素材，
#: 错却出在提示词长度上，很不好解释。所以在发布（粗判）与 `compile`（按展开后精确判）
#: 两处提前挡。
#:
#: 这个数是**执行端点**的属性，不是模板的属性：替换链吃 20000 级别的提示词（P3 之前的
#: 旧模板就是这样）。将来第二个执行端点进来时，它应该随该能力声明，而不是改这里。
#: 漂移由 `tests/test_template_spec_api.py::test_execution_prompt_limit_matches_task_schema` 钉住。
EXECUTION_PROMPT_LIMIT = 2000


@dataclass(frozen=True, slots=True)
class InputSlot:
    """一个输入槽位。

    `source` 与 `binding` 是**两个正交的轴**，四种组合都合法：

    | | `binding=fixed`（封装时定） | `binding=user`（模板库填） |
    |---|---|---|
    | `source=asset` | 绑定某一张图/一段视频 | 模板库挑一张（`min/max` 可多值） |
    | `source=pack` | 绑定某个资产库封装 | 模板库挑一个资产库封装 |

    `source=pack` 的意义：一组"同一对象的多个视图"（产品三视图、人物多视身份）
    结构归**资产库封装**，模板只引用它的 id。这样模板里不会长出第二份成员清单，
    库改了成员模板立刻跟着变。
    """

    key: str
    type: SlotType
    source: SlotSource = "asset"
    binding: SlotBinding = "user"
    ref_mode: RefMode = "mixed"
    #: 只有 `source=asset` 且 `binding=user` 时才有意义；`source=pack` 的数量由成员数决定。
    min_count: int = 1
    max_count: int = 1
    label: str = ""
    #: 进提示词/执行请求的用途说明（"确定同款同色外观""只补纹理与扣件"）。
    usage: str = ""
    asset_id: str | None = None
    pack_id: str | None = None
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

    `assets` **必须已经是最终顺序**：`source=pack` 的顺序由 `expand_packs()` 按
    资产库的 `position` 给出，编译不得重排（重排就等于模板偷偷定义了一份成员顺序）。
    """

    slot_key: str
    assets: tuple[FilledAsset, ...] = ()
    #: 仅 `source=pack` 有值，用于报错时说清是哪个封装。
    pack_id: str | None = None
    pack_name: str = ""


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
