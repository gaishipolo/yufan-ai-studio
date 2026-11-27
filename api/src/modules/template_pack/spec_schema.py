"""模板规格的 HTTP 契约：入参严格（`extra="forbid"`），出参与入参同形。

**出参复用入参的嵌套体**（`SpecSlotOut` 直接继承 `SpecSlotIn`）：形状一致，
不另造一份会漂移的副本。这与 `manifest_schema` 的取舍一致。

## 为什么 `draft` 与 `latest` 分开返回

`draft` 是"正在编辑、还没发布"的那份；`latest` 是"上一次真正对外"的快照。
两者同时给出，页面才能显示"有未发布的改动"。只给一份会让"发布后改了草稿"
看起来像已经生效。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from core.schema import CamelModel, UtcDateTime
from core.template_spec import (
    EXECUTION_PROMPT_LIMIT,
    MAX_ALLOWED_MODELS,
    MAX_PROMPT,
    MAX_SLOT_TAGS,
    MAX_SLOTS,
    RefMode,
    SlotBinding,
    SlotType,
)

#: 槽位 key（进 `{{slot:key}}`，只允许下划线）
_KEY = r"^[a-z][a-z0-9_]{0,39}$"
#: 规格 key（URL 段，允许连字符）
_SPEC_KEY = r"^[a-z][a-z0-9_-]{0,39}$"
#: 封装容器 key（空串 = 不属于任何容器）
_GROUP = r"^(|[a-z][a-z0-9_]{0,39})$"


class StrictIn(CamelModel):
    """入参拒绝未声明的键：少一个拼写错误就变成"配置被静默忽略"。"""

    model_config = ConfigDict(extra="forbid")


class SpecGroupIn(StrictIn):
    """封装容器的名字：`key` 对应槽位上的 `group`，`label` 是作者起的名字。

    **不在这里校验「key 必须真的有成员」**：草稿阶段作者把成员挪走了、容器名
    还留着，是编辑中的正常状态；发布时它只是写不进任何一行（没有成员就没有行），
    不会变成坏数据。反过来强校验会挡住「先建容器、再加槽位」这个正常顺序。
    """

    #: 与槽位的 `group` 同一套取值，但**不能为空**（空串是「不属于任何容器」）。
    key: str = Field(pattern=_KEY)
    label: str = Field(default="", max_length=80)


class SpecSlotIn(StrictIn):
    key: str = Field(pattern=_KEY)
    #: 槽位要什么：就是资产库的 kind（图片 / 视频 / 音频 / 文本）。
    type: SlotType
    binding: SlotBinding = "user"
    ref_mode: RefMode = "mixed"
    #: 所属的**封装容器** key（空 = 独立槽位）。
    #:
    #: **它同时决定素材从哪来**：空 → 从候选素材里挑；非空 → 这一组共用一次
    #: 「选包」，只能从封装包取。所以没有单独的「来源」字段（`pick_mode` 已于
    #: 2026-11-20 删除 —— 它和这一列表达同一件事，两处并存时会出现「在容器里、
    #: 却声明从素材取」这种没法解释的组合）。
    #:
    #: 同一容器的槽位共用一次选包：模板库只出一个「选包」网格，点一次按标签
    #: 把成员分到各槽位。容器内只能是用户填的图片/视频（否则 400），
    #: 且顺序上必须相邻。
    group: str = Field(default="", pattern=_GROUP)
    #: 一槽一张：`min` 只有 0（可选）/ 1（必填），`max` 恒为 1。
    #: 硬约束而非读入时钳制 —— 库里已无多值槽位（迁移 `4e06879f9f75` 清掉了
    #: 最后两个多值 e2e 模板），传 >1 就是真错，应当 422 让人看见。
    min_count: int = Field(default=1, ge=0, le=1)
    max_count: int = Field(default=1, ge=1, le=1)
    label: str = Field(default="", max_length=80)
    asset_id: str | None = Field(default=None, max_length=32)
    #: **选中时文案**：使用者填了素材时，提示词里这一段怎么说。
    #:
    #: 提示词正文里 `{{slot:key}}` 是**唯一插入点**，这一段的措辞跟着槽位走 ——
    #: 写在正文里就得让作者把同一句话维护两遍（正文一遍、槽位一遍），
    #: 两处措辞一漂移，编译时就对不上。
    #:
    #: 为空 = 直接插入 `{{Mixed N}}`（旧行为，现有模板一字不改也能继续用）。
    #: 可含 `{{slot:key}}` **自引用**（派生成编号），例如「人物参考 {{slot:hero}}」。
    #: 也可引用**必定有素材**的槽位（必填 / 已固定）—— 那种槽位每次编译都有编号；
    #: 可选槽位不许引用（可能没人填，那时没有编号可指）。
    filled_text: str = Field(default="", max_length=2000)
    #: **未选时文案**：使用者**没填**素材时，提示词里这一段怎么说。
    #:
    #: 只对**可选槽位**（`minCount == 0`）有意义。为空 = 编译期报错
    #: （提示词悄悄缺一段比报错更糟）。
    #: 可引用**必定有素材**的槽位（必填 / 已固定），但**不许引用它自己** ——
    #: 这段话用的正是「使用者没给这一格挑素材」的时候，它那时没有编号可指。
    empty_text: str = Field(default="", max_length=2000)
    #: **未选时文案是否来自正文里的框选段**（2026-11-27）。
    #:
    #: 可选槽位的「未选时文案」按设计是正文里框选出来的那一段：提交时那段
    #: 正文让位成插入点，文字挪进 empty_text。记下这一点，打开草稿时才知道
    #: 该把哪一段还原成可编辑的文字 —— 否则「库里存的正文」与「打开后看到的
    #: 正文」不一致，作者原话会被兜底文案顶掉。
    #:
    #: 老数据为 False → 预检要求作者在正文里重新框选一次。
    empty_text_from_anchor: bool = False
    #: 这一格**怎么选**：`asset`（默认，从候选素材里挑）/ `pack`（只从封装包里选）。
    #: 候选集的标签 id（资产库 `library_tags`，**任意级别**）：**候选集范围**，
    #: 不是内容。空 = 不限；非空 = 只在这几个标签（各自及其下级）里挑；编译不看它。
    #:
    #: 标签可以是分组、系列或具体词：槽位的标签是"圈定候选范围"，不是"打在
    #: 素材上"，所以不必限叶子。多个标签之间是并集。
    tag_ids: list[str] = Field(default_factory=list, max_length=MAX_SLOT_TAGS)
    position: int = Field(default=0, ge=0, le=MAX_SLOTS * 10)

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_tag_id(cls, data: Any) -> Any:
        """老槽位只有 `tagId`（单个），读回来时当成 `tagIds` 的第一项。

        为什么要有这条：`extra="forbid"` 会把老草稿里的 `tagId` 判成"未知键"，
        那份草稿**一打开就 422** —— 模板还在，只是没人能再编辑它。老模板要能
        继续用、也能继续改，所以在这里把旧键翻译成新形状，而不是逼人去改库。

        新的 `tagIds` 优先：两者同时出现时以新键为准（旧键只是被丢掉，不报错，
        免得升级途中把一份还没保存的草稿挡在门外）。
        """
        if not isinstance(data, dict) or "tagId" not in data:
            return data
        payload = dict(data)
        legacy = payload.pop("tagId")
        if (
            not payload.get("tagIds")
            and not payload.get("tag_ids")
            and isinstance(legacy, str)
            and legacy
        ):
            payload["tagIds"] = [legacy]
        return payload

    @model_validator(mode="before")
    @classmethod
    def _drop_legacy_usage(cls, data: Any) -> Any:
        """老草稿的槽位里还带着 `usage`（「进提示词的用途说明」），读回来时丢掉。

        为什么要有这条：`usage` **从来没有进过提示词** —— 编译器完全不看它，
        它只在前端显示一行字。作者填了它，模板库的使用者会看到一句
        「确定同款同色外观」，而提示词里根本没有这句话 —— 说了等于没说，
        还会让人以为那句描述真的管用。

        所以这个字段已经删掉（迁移 `d7f2a91c48b3` 删列）。但 `extra="forbid"`
        会把老草稿里的 `usage` 判成「未知键」—— 那份草稿**一打开就 422**，
        模板还在，只是没人能再编辑它。老模板要能继续用、也能继续改，
        所以在契约层把旧键丢掉，而不是逼人去改库。

        与老 `tagId` → `tagIds`、`packId` 丢弃是同一个形状的处理。
        """
        if not isinstance(data, dict) or "usage" not in data:
            return data
        payload = dict(data)
        payload.pop("usage", None)
        return payload

    @model_validator(mode="before")
    @classmethod
    def _drop_legacy_pack_fields(cls, data: Any) -> Any:
        """老草稿的槽位里还带着 `packId`（素材封装下线前的字段），读回来时丢掉。

        为什么要有这条：素材封装下线后槽位契约里没有 `packId` 了，而
        `extra="forbid"` 会把老草稿里的这个键判成「未知键」——那份草稿**一打开就
        422**，整页模板列表跟着 500（模板都还在，只是没人能读它）。与老 `tagId` →
        `tagIds` 是同一个形状：老模板要能继续用、也能继续改，所以在契约层把旧键丢掉，
        而不是逼人去改库。

        封装槽位本身在迁移 `c3d8e1f04a7b` 里已经改成空图片槽位了，这里丢的只是一个
        指向已下线功能的残留引用，丢不丢都不影响图片槽位。
        """
        if not isinstance(data, dict):
            return data
        legacy = [
            key for key in ("packId", "packVersion", "pack_id", "pack_version") if key in data
        ]
        if not legacy:
            return data
        payload = dict(data)
        for key in legacy:
            payload.pop(key, None)
        return payload


class TemplateSpecIn(StrictIn):
    """一份规格（草稿的整份内容）。`key` 是幂等边界，发布后不可改名。"""

    key: str = Field(pattern=_SPEC_KEY)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    model: str = Field(min_length=1, max_length=80)
    #: 使用模板时**允许选用的模型清单**。空 = 只有 `model` 那一个（老模板不受影响）。
    #:
    #: 作者声明「这份模板能跑在哪几款模型上」，使用者从这份清单里挑，而不是
    #: 只能用一个写死的模型。清单里的 id 必须存在于模型目录（发布时校验）。
    allowed_models: list[str] = Field(default_factory=list, max_length=MAX_ALLOWED_MODELS)
    params: dict[str, Any] = Field(default_factory=dict)
    #: 整段提示词。需要可变的地方用 `text` 槽位，不要指望模板库改文字。
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT)
    #: **随机档提示词**：作者给「随机」档另写的一整段提示词，随机档时整段替换
    #: `prompt`。空 = 该模板不支持随机档（模板库把这一档置灰并说明原因）。
    #:
    #: 引用规则与 `prompt` 相同；因为随机档不传可选素材，它引用可选槽位时
    #: 同样要求该槽位写了「未选时文案」（发布闸口会拦）。
    random_prompt: str = Field(default="", max_length=MAX_PROMPT)
    slots: list[SpecSlotIn] = Field(default_factory=list, max_length=MAX_SLOTS)
    #: 封装容器的名字。**可以不声明**（老草稿也没有这个键）：缺省 = 每个容器都
    #: 用回退名「封装槽位 N」，前端按段序算出来。
    groups: list[SpecGroupIn] = Field(default_factory=list, max_length=MAX_SLOTS)
    attributes: dict[str, str] = Field(default_factory=dict)
    #: 展示案例（图片/视频资产 id）。纯展示：不进发布快照，改图不用发新版。
    demo_asset_id: str | None = Field(default=None, max_length=32)
    #: 乐观锁：与 head 上的 `revision` 比对。为空表示"不校验版本"（首次保存）。
    revision: int | None = Field(default=None, ge=0)

    @field_validator("params")
    @classmethod
    def _params_are_flat(cls, value: dict[str, Any]) -> dict[str, Any]:
        # 参数最终原样进执行请求；嵌套结构在执行器那侧只会变成 400 或静默忽略。
        if any(isinstance(item, (dict, list)) for item in value.values()):
            raise ValueError("参数只能是标量（字符串 / 数字 / 布尔）")
        return value

    @field_validator("groups")
    @classmethod
    def _group_keys_are_unique(cls, value: list[SpecGroupIn]) -> list[SpecGroupIn]:
        """同一个容器 key 出现两次 → 哪个名字算数说不清，直接 422。

        前端已经按 key 维护唯一容器（`specDraft` 的 `groups`），重复只可能是
        调用方自己拼的请求；静默取第一个会让「改过的名字没生效」且无从解释。
        """
        seen: set[str] = set()
        for group in value:
            if group.key in seen:
                raise ValueError(f"封装容器 key 重复：{group.key}")
            seen.add(group.key)
        return value


class SpecSlotOut(SpecSlotIn):
    """槽位出参：与入参同形。"""


class SpecVersionOut(CamelModel):
    """一次发布快照。`slots` 的顺序就是编号顺序（`position` 升序返回）。"""

    version: int
    model: str
    #: 使用模板时允许选用的模型清单（发布时冻结）。空 = 只有 `model` 那一个。
    allowed_models: list[str] = Field(default_factory=list)
    params: dict[str, Any]
    prompt: str
    #: 「随机」档用的整段提示词（发布时冻结）。空 = 该版本不支持随机档。
    random_prompt: str = ""
    slots: list[SpecSlotOut]
    #: 封装容器的名字（发布时冻结）。**老版本为空**：那时还没这个字段，
    #: 前端按段序回退成「封装槽位 N」。
    groups: list[SpecGroupIn] = Field(default_factory=list)
    fingerprint: str = ""
    published_by: str = ""
    published_at: UtcDateTime | None = None


class TemplateSpecOut(CamelModel):
    id: str
    key: str
    name: str
    description: str
    owner_id: str
    status: Literal["draft", "published", "disabled"]
    revision: int
    fingerprint: str
    current_version: int
    published_at: UtcDateTime | None = None
    disabled_at: UtcDateTime | None = None
    #: 软删时间。非空 = 在回收站里，列表与模板库都看不到它。
    deleted_at: UtcDateTime | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    #: 正在编辑的整份内容；为空表示还没保存过。
    draft: TemplateSpecIn | None = None
    #: 展示案例（图片/视频资产 id）。存在草稿里，不进发布快照 —— 改案例不用发新版。
    demo_asset_id: str | None = None
    #: 模板自身的标签 id（template_tags）。独立于 draft，改了立即生效、不发版。
    tag_ids: list[str] = Field(default_factory=list)
    #: 最近一次发布；为空表示从未发布。
    latest: SpecVersionOut | None = None


class TemplateSpecListOut(CamelModel):
    items: list[TemplateSpecOut] = Field(default_factory=list)
    total: int = 0


class SpecValidateOut(CamelModel):
    valid: bool = True
    fingerprint: str


class SpecPublishOut(CamelModel):
    version: int
    spec: TemplateSpecOut


class SpecCompileIn(StrictIn):
    """填表结果：`{slotKey: [assetId…]}`。

    槽位按用户挑选顺序传素材 id。`version` 为空表示用当前发布版本。

    **没有 `promptOverride`**（2026-11-14 起）：模板库不改整段文字。需要可变内容
    的地方在模板里放 `text` 槽位 —— 填表时选一份文案，`compile` 把它排在
    `{{Text N}}` 的第 N 位，执行层按 `textLibraryIds` 注入正文。这个入口曾经存在
    （改写"可编辑段"），与"提示词只有一段"冲突，一并删掉。

    但**单个可选槽位的「未选时文案」可以改**（`slot_text_overrides`，2026-11-21）：
    那段话本来就是「这一格空着时说什么」，作者写的是通用版本，使用者知道得更具体。
    改的是插入点里的那一段，不是提示词的骨架 —— 与上面删掉的那个入口不是一回事。
    """

    version: int | None = Field(default=None, ge=1)
    #: 使用者挑的模型。必须落在该版本允许的清单里（清单为空 = 只有固定模型）。
    #:
    #: 不传 = 用模板的固定模型（老调用方的默认行为）。传了而清单里没有它，
    #: 直接 400 —— 静默改成固定模型等于「用户选的东西没生效」，而页面看起来
    #: 一切正常。
    model: str | None = Field(default=None, max_length=80)
    slot_values: dict[str, list[str]] = Field(default_factory=dict)
    #: 「随机」档：整段改用该模板的 `randomPrompt` 编译。
    #:
    #: 由**使用者**在模板库选档时决定，不是作者——作者只决定「有没有这一段」。
    #: 模板没配 `randomPrompt` 时传 `true` 会 400（前端也会把该档置灰），
    #: 不静默回退主提示词 —— 回退的样子是「随机档和均衡档产出一模一样」。
    #: 不传 = 均衡 / 自定义档，走 `prompt`（老调用方零影响）。
    use_random_prompt: bool = False
    #: 使用者改写「未选时文案」：`{slotKey: 改后的那段话}`。
    #:
    #: 「这一格我不挑素材，直接说我想要什么」—— 改的只是作者写的那段兜底话，
    #: 不是整段提示词（那个入口 2026-11-14 删了，与「提示词只有一段」冲突）。
    #: 编译前替换该槽位的 `empty_text`，编号展开仍由 `compile_spec` 一处算。
    #:
    #: 只对「可选、且提示词里有插入点」的槽位有效。不满足就 400 —— 静默收下的
    #: 后果是「用户改的话没进提示词，页面看起来一切正常」。
    slot_text_overrides: dict[str, str] = Field(default_factory=dict)
    #: 使用者在填表时对「运行时参数」的选择（如计费模式）。
    #:
    #: 这些参数在模型 schema 里标了 `userSelectable`：**不该由模板作者写死**
    #: ——写死等于作者替每个使用者决定了要不要花积分，而积分是用的人的钱。
    #: 作者设了会在预检/发布时报错，使用者的选择则在这里传进来，
    #: 不落进 spec.params（那是作者的部分）。
    param_overrides: dict[str, Any] = Field(default_factory=dict)


class CompiledSlotOut(CamelModel):
    """哪几张素材拿到了哪个编号 —— 只给前端画角标，不参与提交。"""

    key: str
    tokens: list[str] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)


class SpecCompileOut(CamelModel):
    """编译结果（模板层）。

    **不是**任何端点的请求体：`/tasks` 的时长与画幅是顶层字段、不接受自由
    `params`。摊平由提交方按目标端点做（模板库页面对照生成的 `CreateTaskIn` 类型），
    这里刻意不假设端点，否则换端点就要改编译规则。
    """

    version: int
    model: str
    prompt: str
    params: dict[str, Any]
    reference_library_ids: list[str] = Field(default_factory=list)
    text_library_ids: list[str] = Field(default_factory=list)
    slots: list[CompiledSlotOut] = Field(default_factory=list)


class SpecLimitsOut(CamelModel):
    """编辑页要显示的**上限**。

    这些数是后端的常量（`core/template_spec/models.py`），前端以前各抄一份
    常量 + 一条「对着 openapi 读一遍」的漂移守卫。改成从接口取之后，
    只有一处真值 —— 调上限只改后端。

    两个数说的是不同的事，别混：

    - `promptMax`：**存草稿**的上限（20000）。草稿允许很长，写一半也能存。
    - `promptExecutionLimit`：**能不能发布**的上限（7000），按**原文字数**比。

    编辑页显示的是后者：它是真正会拦住作者的那个数。注意它只是**下限保证** ——
    提示词展开（`{{Mixed 1*}}` 一次列出整组）之后还可能更长，那一步的判据在
    编译期（`compile` 按展开后的成品长度再比一次）。
    """

    prompt_max: int = MAX_PROMPT
    prompt_execution_limit: int = EXECUTION_PROMPT_LIMIT


# ---- 模板独立标签词表（2026-11-27）----
# 与素材库词表互不影响：模板的分类轴是另一回事。标签存在 head 的 tag_ids
# JSON 列，改了立即生效，不进发布快照。

#: 一个模板最多挂多少标签（与 tags.py 同口径）。
MAX_SPEC_TAGS = 40
MAX_TAG_NAME = 40


class TagOut(CamelModel):
    """词表里的一条模板标签（平铺，两级：`parentId` 空 = 一级维度）。"""

    id: str
    name: str
    parent_id: str | None = None
    #: 父的整条链；两级下就是一级维度名（如「用途」）。
    parent_name: str | None = None
    #: 1 = 维度（分组），2 = 取值（可打在模板上）
    depth: int = 1
    #: 挂了这个标签（或其下级）的模板数
    spec_count: int = 0


class TagIn(StrictIn):
    """新建 / 重命名标签。`parentId` 留空 = 一级维度。"""

    name: str = Field(min_length=1, max_length=MAX_TAG_NAME)
    parent_id: str | None = Field(default=None, max_length=32)


class TagParentIn(StrictIn):
    """移动层级。`parentId` 留空 = 提升为一级。"""

    parent_id: str | None = Field(default=None, max_length=32)


class SpecTagsIn(StrictIn):
    """给一个模板打标签：交回**完整** id 数组（空数组 = 清空）。"""

    tag_ids: list[str] = Field(default_factory=list, max_length=MAX_SPEC_TAGS)

    @field_validator("tag_ids")
    @classmethod
    def _no_dupes(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("标签不能重复")
        return value
