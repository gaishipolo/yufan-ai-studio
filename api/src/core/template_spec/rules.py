"""规格的结构校验：不碰数据库、不碰资产库，只回答"这份规格本身合不合法"。

分成独立文件而不是塞进 `compile.py`：封装页在用户点"预检"时要能只校验结构
（还没填过任何素材），而 `compile` 需要一份完整填充。两者的入参不同，混在一起
会让"没有填充时的校验"变成一堆可选参数。
"""

from __future__ import annotations

import re
from dataclasses import replace

from core.exceptions import BadRequest
from core.template_spec.models import (
    MAX_ALLOWED_MODELS,
    MAX_PROMPT,
    MAX_SLOT_TAGS,
    MAX_SLOTS,
    SLOT_TYPES,
    InputSlot,
    TemplateSpec,
)

#: 槽位 key：会出现在 `{{slot:key}}` 里，只允许下划线（连字符会让引用语法更难读）。
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
#: 规格 key：URL 段 + 运营可读，允许连字符（`product-swap-6036`）。
_SPEC_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
#: 规格里**不许**出现 `{{Text N}}`：文本槽位在第几份由槽位顺序决定，
#: 写死会在少选一份时错位。（`{{Mixed N}}` 不一样 —— 它现在是**作者的引用写法**，
#: 见 `_MIXED_REF`。）
_TEXT_LITERAL = re.compile(r"\{\{\s*Text\s+\d+\s*\}\}")
#: 作者写的视觉槽位引用：`{{Mixed N}}`，N = 图片/成片槽位在**槽位顺序**里的序号。
#:
#: 这是 2026-11-19 改的：以前作者写 `{{slot:image_1}}`（内部 key），
#: 编号只在编译产物里出现。两者说的是同一件事，作者却要记两套写法 ——
#: 统一成 `{{Mixed N}}` 之后，作者写的和模型看到的是同一种东西。
#:
#: 编号按**槽位顺序**数（只数 `ref_mode == "mixed"` 的槽位）。注意它与编译产物
#: 里的编号**可能不同**：编译时若某个槽位填了 2 张，它占 1、2 两个号，后面的
#: 槽位整体后移。作者写的 `{{Mixed N}}` 指的是「第 N 个槽位」，展开成该槽位
#: 实际拿到的编号（可能是一串）。
_MIXED_REF = re.compile(r"\{\{\s*Mixed\s+(\d+)(\*|\.\d+)?\s*\}\}")
#: 提示词里的槽位引用：`{{slot:key}}` / `{{slot:key*}}` / `{{slot:key.N}}`。
#: 抓 key 是为了核对它有没有对应槽位 —— 引用一个已删掉的槽位，`compile` 才会报
#: 「引用了不存在的槽位」，而预检那一步会放行（它只管结构）。槽位可以删到 0，
#: 这条就更容易踩到，所以把判据提到结构校验里。
#:
#: **两个捕获组**：1 = 槽位 key，2 = 修饰符（`""` / `"*"` / `".N"`）。
#: 修饰符要抓出来，是因为三种写法的「没填素材」语义不同（见
#: `slot_ref_modes`）：单张引用要兜底文案，整组引用降级为空，第 N 张必然报错。
#: 注意 `findall` 在有两个组时返回**元组** —— 要 key 请用 `finditer` 取
#: `group(1)`（`slot_refs` / `slot_ref_modes` 就是这么做的）。
_SLOT_REF = re.compile(r"\{\{\s*slot:([a-z0-9_]+)(\*|\.\d+)?\s*\}\}")
#: 替换后还残留这种片段，说明引用写坏了（少括号、key 形状不对、编号位数超限等）。
#:
#: **必须容空格**：上面的 `_SLOT_REF` 已经允许 `{{ slot:key }}`，那么坏掉的形态
#: 同样带空格（`{{ slot:Key }}`、`{{ slot:key.1234 }}`）。判据要是写成字面量
#: `"{{slot:"`，这些坏引用会同时躲过替换与残留检查 —— 原样进入供应商提示词，
#: 用户看到的是模型吐出一坨带大括号的乱码，而日志里什么线索都没有
#: （2026-11-15 审查 4.1）。
#:
#: **只在这里定义一次**（`compile.py` 从这里导入）：这个形状在
#: `rules` / `compile` / 前端 `specDraft.slotRefPattern` 三处各有一份，
#: 已经栽过三次（形状漂移 → 带空格的坏引用躲过检查 → 原样进供应商提示词）。
#: 再抄一份就是第四次。
_LEFTOVER = re.compile(r"\{\{\s*slot:")


def ordered_slots(spec: TemplateSpec) -> tuple[InputSlot, ...]:
    """按 `position` 排好的槽位。编号与展开都按这个顺序算 —— 只在这里排。"""
    # 与 `compile.py` 的排序键一致（`position` 相同时按 key）——
    # 两处顺序一旦不同，作者写的 Mixed 3 与编译算出的编号就会指向不同槽位。
    return tuple(sorted(spec.slots, key=lambda slot: (slot.position, slot.key)))


def visual_slot_keys(spec: TemplateSpec) -> list[str]:
    """参与 `{{Mixed N}}` 编号的槽位 key（图片 / 成片），按槽位顺序。

    判据是 `ref_mode == "mixed"`：文本与音频是附加输入（`attachment`），
    不占视觉编号。这与 `compile.py` 里 `counter` 只在非 attachment 分支累加
    是**同一个判据** —— 两处漂移的表现是「作者写的 Mixed 3 指向了另一个槽位」。
    """
    return [slot.key for slot in ordered_slots(spec) if slot.ref_mode == "mixed"]


def normalize_refs(spec: TemplateSpec) -> TemplateSpec:
    """把作者写的 `{{Mixed N}}` 换算成内部的 `{{slot:key}}`（幂等）。

    校验、编译、`slot_ref_modes` 全都只认 `{{slot:key}}`。与其在每处各写一份
    解析，不如在**入口换算一次**：`validate_spec` / `compile_spec` /
    `_check_executable` 三个入口各调一次，后面的逻辑一个字都不用改。

    越界的 N 直接报错。作者写了 `{{Mixed 5}}` 而这里只有 3 个图片/成片槽位，
    等编译期才发现的话，错误会出现在**别人点生成**的时候 —— 那时他既不知道
    这份模板是谁写的，也看不出该怎么改。
    """
    keys = visual_slot_keys(spec)

    def fix(text: str, where: str) -> str:
        if not text or "Mixed" not in text:
            return text

        def repl(match: re.Match[str]) -> str:
            index = int(match.group(1))
            if not 1 <= index <= len(keys):
                raise BadRequest(
                    f"{where}里的 {{{{Mixed {index}}}}} 找不到对应槽位："
                    f"图片/成片槽位只有 {len(keys)} 个"
                )
            return f"{{{{slot:{keys[index - 1]}{match.group(2) or ''}}}}}"

        return _MIXED_REF.sub(repl, text)

    def name_of(slot: InputSlot) -> str:
        return slot.label.strip() or slot.key

    return replace(
        spec,
        prompt=fix(spec.prompt, "提示词"),
        slots=tuple(
            replace(
                slot,
                filled_text=fix(slot.filled_text, f"槽位「{name_of(slot)}」的选中时文案"),
                empty_text=fix(slot.empty_text, f"槽位「{name_of(slot)}」的未选时文案"),
            )
            for slot in spec.slots
        ),
    )



def validate_spec(spec: TemplateSpec) -> None:
    """校验槽位声明与提示词形状。任一不合法就抛 `BadRequest`（带可读原因）。

    入口先把作者写的 `{{Mixed N}}` 换算成 key（`normalize_refs`）：
    后面全部逻辑只认一种写法，不必到处兼容两套语法。
    """
    spec = normalize_refs(spec)
    if not _SPEC_KEY.match(spec.key):
        raise BadRequest(
            "模板 key 只能是小写字母开头、含小写字母/数字/下划线/连字符的 1~40 字"
        )
    if not spec.name.strip():
        raise BadRequest("模板名称不能为空")
    if not spec.model.strip():
        raise BadRequest("必须选择模型")
    _validate_allowed_models(spec.allowed_models)
    if len(spec.slots) > MAX_SLOTS:
        raise BadRequest(f"槽位不能超过 {MAX_SLOTS} 个")

    keys: set[str] = set()
    positions: set[int] = set()
    for slot in spec.slots:
        if not _KEY.match(slot.key):
            raise BadRequest(f"槽位 key 不合法：{slot.key}")
        if slot.key in keys:
            raise BadRequest(f"槽位 key 重复：{slot.key}")
        keys.add(slot.key)
        if slot.position in positions:
            raise BadRequest(f"槽位位置重复：{slot.position}")
        positions.add(slot.position)
        _validate_slot(slot)

    for ref in sorted(slot_refs(spec.prompt)):
        if ref not in keys:
            raise BadRequest(
                f"提示词引用了不存在的槽位「{ref}」：删槽位时要把它的引用一并删掉"
            )

    _validate_slot_texts(spec)

    if _TEXT_LITERAL.search(spec.prompt):
        raise BadRequest(
            "提示词不要直接写 {{Text N}}：文本槽位排第几由槽位顺序决定，"
            "写死会在少选一份时错位"
        )
    if len(spec.prompt) > MAX_PROMPT:
        raise BadRequest(f"提示词不能超过 {MAX_PROMPT} 字（现在 {len(spec.prompt)}）")
    if not spec.prompt.strip():
        raise BadRequest("提示词不能为空")


def _validate_allowed_models(models: tuple[str, ...]) -> None:
    """「允许选用的模型清单」的结构校验。

    只管**形状**（不碰目录）：模型 id 是否真的存在由 `_check_model` 在发布前查
    目录 —— 结构校验是纯函数，不碰 session。

    重复项要挡：同一个模型写两遍不会让使用者多一个选项，只会让清单看起来像
    「作者手滑」，而清单是要给人读的。
    """
    if len(models) > MAX_ALLOWED_MODELS:
        raise BadRequest(f"允许选用的模型不能超过 {MAX_ALLOWED_MODELS} 个")
    seen: set[str] = set()
    for model_id in models:
        if not model_id.strip():
            raise BadRequest("允许选用的模型 id 不能为空")
        if len(model_id) > 80:
            raise BadRequest(f"允许选用的模型 id 不合法：{model_id}")
        if model_id in seen:
            raise BadRequest(f"允许选用的模型重复：{model_id}")
        seen.add(model_id)


def has_bad_slot_ref(text: str) -> bool:
    """这段文字里有**无法解析**的槽位引用吗（少括号、key 形状不对、编号位数超限）？

    `_LEFTOVER` 是**替换之后**的残留检查 —— 直接拿它去搜原文会把**每一个**
    合法的引用都判成「坏引用」（`{{slot:hero}}` 本身就匹配 `{{\\s*slot:`）。
    所以这里先把**合法**的引用摘掉，剩下的才是坏的。

    摘掉用同一个正则（`_SLOT_REF`）：合法的形状只有一处定义，摘的时候
    用的形状与替换时一致，才不会出现「校验说合法、替换说非法」。
    """
    return bool(_LEFTOVER.search(_SLOT_REF.sub("", text)))


def slot_refs(prompt: str) -> list[str]:
    """提示词里引用了哪些槽位（去重、保序）。

    与结构校验、编译用的是**同一个**正则（`_SLOT_REF`）：调用方问「这个提示词
    引用了谁」时不该自己再写一份形状 —— 写歪了就会出现「校验说没引用、
    编译说引用了」这种互相打架的结论（这个形状已经漂移过三次）。
    """
    return list(dict.fromkeys(match.group(1) for match in _SLOT_REF.finditer(prompt)))


def slot_ref_modes(prompt: str) -> dict[str, set[str]]:
    """提示词里每个槽位被**哪种写法**引用了。

    返回 `{槽位key: {"", "*", ".2"}}`，其中 `""` = 单张引用 `{{slot:x}}`，
    `"*"` = 整组引用 `{{slot:x*}}`，`".2"` = 第 N 张 `{{slot:x.2}}`。

    **为什么要分写法**：三种写法的「没填素材」语义不同（见 `compile.py`
    的说明）—— 单张引用要兜底文案，整组引用降级为空，第 N 张直接报错。
    只问「引用了谁」判不出该不该要兜底，所以这里把写法也带出来。
    """
    modes: dict[str, set[str]] = {}
    for match in _SLOT_REF.finditer(prompt):
        key, modifier = match.group(1), match.group(2) or ""
        modes.setdefault(key, set()).add(modifier)
    return modes


def _validate_slot_texts(spec: TemplateSpec) -> None:
    """校验「选中时文案」与「未选时文案」。

    两条约束各管一件事：

    1. **`filled_text` 只能引用自己**。它是「这一段在有素材时怎么说」，
       里面出现别的槽位会让编译期的展开顺序变得有依赖（这个槽位的文案里
       要先算另一个槽位的编号）—— 而编号是**按槽位顺序**算的，跨槽位引用
       在「另一个槽位排在前面」和「排在后面」两种情况下的编号不一样，
       同一段文案编译出两种结果。这种依赖没人能推理得清，直接禁掉。
       自引用（`{{slot:key}}`）是允许的，而且是**推荐的写法** ——
       「人物参考 {{slot:hero}}」里的那个编号就靠它派生成。

    2. **`empty_text` 不许含任何槽位引用**。没填素材时根本没有编号可指，
       写了就是错的。留到编译期才发现的话，作者已经填完素材了。

    还有一条**不是**校验、而是**形状对齐**：可选槽位（`min_count == 0`）
    在提示词里被引用时，如果既没有 `empty_text` 也没有「整组引用」的写法，
    那么使用者不填素材就会在编译期报错。这条**不在这里拦** —— 草稿允许
    写一半（`validate_spec` 也被保存草稿调用），拦了会逼作者先想清楚
    兜底文案再保存。真正的闸门在发布（`_check_executable`）与编译期。

    ## 第三条：必填槽位不许带两段文案

    **必填 = 素材一定在 = 直接插编号**（`compile.py` 的既有行为）。必填槽位
    根本没有「没素材」这个状态，所以两段文案对它**没有意义**：
    - `empty_text` 永远不会用到（不填素材在编译期就直接报错）；
    - `filled_text` 是在「素材一定在」的前提下给编号换个说法 ——
      那等于「必填槽位也能自定义措辞」，与「必填 = 直接插编号」这条
      简单规则冲突，而且作者会为了它去记「哪些槽位能写文案」。

    这里**报错而不是静默忽略**：作者在界面上把槽位从「可选」切成「必填」，
    之前写的文案就没用了 —— 如果后端默默丢掉，用户会以为「我写了文案怎么
    没生效」；如果后端默默保留并照用，那条文案就在必填槽位上悄悄生效，
    与界面上说的「必填 = 直接插编号」不符。两头都比报错更坏。
    界面在切成必填时也会主动清掉并提示（见 `SpecWorkspace.patchSlot`）。
    """
    for slot in spec.slots:
        label = slot.label.strip() or slot.key
        if slot.min_count >= 1 and (
            slot.filled_text.strip() or slot.empty_text.strip() or slot.random_texts
        ):
            raise BadRequest(
                f"槽位「{label}」是必填的（最少数 {slot.min_count}），"
                "不能写「选中时文案 / 未选时文案 / 随机文案」："
                "必填槽位一定有素材，提示词里直接插入素材编号。"
                "要让素材可以被省略，请把它改成「可选」。"
            )
        if slot.filled_text:
            for ref in sorted(slot_refs(slot.filled_text)):
                if ref != slot.key:
                    raise BadRequest(
                        f"槽位「{label}」的选中时文案引用了别的槽位「{ref}」："
                        "它只能引用自己（编号按槽位顺序算，跨槽位引用算不准）"
                    )
            if has_bad_slot_ref(slot.filled_text):
                raise BadRequest(
                    f"槽位「{label}」的选中时文案里有无法解析的槽位引用"
                    "（检查括号与槽位 key）"
                )
        if slot.empty_text:
            refs = slot_refs(slot.empty_text)
            if refs:
                raise BadRequest(
                    f"槽位「{label}」的未选时文案不能引用槽位（{'、'.join(refs)}）："
                    "没有素材就没有编号可指"
                )
            if has_bad_slot_ref(slot.empty_text):
                raise BadRequest(
                    f"槽位「{label}」的未选时文案里有无法解析的槽位引用"
                    "（检查括号与槽位 key）"
                )
        # 随机文案与「未选时文案」同为「没填素材时怎么说」，约束一致：
        # 不许引用槽位（没素材就没有编号可指）、括号要闭合。
        for index, text in enumerate(slot.random_texts, 1):
            refs = slot_refs(text)
            if refs:
                raise BadRequest(
                    f"槽位「{label}」的第 {index} 条随机文案不能引用槽位"
                    f"（{'、'.join(refs)}）：没有素材就没有编号可指"
                )
            if has_bad_slot_ref(text):
                raise BadRequest(
                    f"槽位「{label}」的第 {index} 条随机文案里有无法解析的槽位引用"
                    "（检查括号与槽位 key）"
                )


def _validate_slot(slot: InputSlot) -> None:
    if slot.type not in SLOT_TYPES:
        raise BadRequest(f"槽位「{slot.key}」的类型无效：{slot.type}")
    # 音频与文本都不该占视觉编号：执行器按位置取图/视频，塞进去会让编号整体偏移。
    if slot.type == "audio" and slot.ref_mode != "attachment":
        raise BadRequest(f"音频槽位「{slot.key}」必须是附加输入（refMode=attachment）")
    if slot.type == "text" and slot.ref_mode != "attachment":
        raise BadRequest(f"文本槽位「{slot.key}」必须是附加输入（refMode=attachment）")

    if slot.binding == "fixed" and not slot.asset_id:
        raise BadRequest(f"槽位「{slot.key}」固定素材时必须提供 assetId")
    if slot.binding == "user" and slot.asset_id:
        raise BadRequest(f"槽位「{slot.key}」由用户选择时不能预置 assetId")
    if slot.min_count < 0 or slot.max_count < 1:
        raise BadRequest(f"槽位「{slot.key}」的数量范围无效")
    if slot.min_count > slot.max_count:
        raise BadRequest(f"槽位「{slot.key}」的最小数量不能大于最大数量")
    # 一槽一张在**写入契约**收紧（spec_schema 的 le=1），这里不重复拒：
    # 编译算法仍支持多值（test_template_spec_compile.py 守卫它的正确性），
    # 那些用例就是靠直接构造 InputSlot 绕过契约层来测的。真实用户写不进
    # 多值 spec，所以入口已经封死，不需要在这里再挡一道。
    if slot.type == "audio" and slot.binding == "user" and slot.max_count > 3:
        raise BadRequest(f"音频槽位「{slot.key}」最多 3 段")
    # 标签只约束候选集，不参与编译；这里只拦"明显不是 id 的字符串"，
    # 不查库（结构校验是纯函数，不碰 session）。
    if len(slot.tag_ids) > MAX_SLOT_TAGS:
        raise BadRequest(f"槽位「{slot.key}」最多圈 {MAX_SLOT_TAGS} 个标签")
    for tag_id in slot.tag_ids:
        if not tag_id or len(tag_id) > 32 or any(c.isspace() for c in tag_id):
            raise BadRequest(f"槽位「{slot.key}」的标签 id 不合法")
