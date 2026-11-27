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
    PACK_GROUP_TYPES,
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


def normalize_text_refs(text: str, spec: TemplateSpec, *, where: str) -> str:
    """把**一段文字**里的作者写法（`{{Mixed N}}`）换成内部写法（`{{slot:key}}`）。

    与 `normalize_refs` 同一套映射（按图片/成片槽位的顺序数），
    区别只是作用对象：**整份规格** vs **一段文字**。

    什么时候需要单独换算：使用者改写「未选时文案」时，那段字还没进规格，
    校验它却得先知道它引用的是哪个槽位。

    越界的编号当场报错：作者写 `{{Mixed 9}}` 而只有 3 个图片/成片槽位，
    这条引用永远解析不出来 —— 留着等到编译期才炸，错误会出现在别人点生成的时候。
    """
    keys = visual_slot_keys(spec)
    if not text or "Mixed" not in text:
        return text

    def repl(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if not 1 <= index <= len(keys):
            marker = "{{Mixed " + str(index) + "}}"
            raise BadRequest(
                f"{where}里的 {marker} 找不到对应槽位："
                f"图片/成片槽位只有 {len(keys)} 个"
            )
        return f"{{{{slot:{keys[index - 1]}{match.group(2) or ''}}}}}"

    return _MIXED_REF.sub(repl, text)


def normalize_refs(spec: TemplateSpec) -> TemplateSpec:
    """把作者写的 `{{Mixed N}}` 换算成内部的 `{{slot:key}}`（幂等）。

    校验、编译、`slot_ref_modes` 全都只认 `{{slot:key}}`。与其在每处各写一份
    解析，不如在**入口换算一次**：`validate_spec` / `compile_spec` /
    `_check_executable` 三个入口各调一次，后面的逻辑一个字都不用改。

    越界的 N 直接报错。作者写了 `{{Mixed 5}}` 而这里只有 3 个图片/成片槽位，
    等编译期才发现的话，错误会出现在**别人点生成**的时候 —— 那时他既不知道
    这份模板是谁写的，也看不出该怎么改。
    """
    def fix(text: str, where: str) -> str:
        return normalize_text_refs(text, spec, where=where)

    def name_of(slot: InputSlot) -> str:
        return slot.label.strip() or slot.key

    return replace(
        spec,
        prompt=fix(spec.prompt, "提示词"),
        random_prompt=fix(spec.random_prompt, "随机档提示词"),
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

    _validate_groups(spec)

    _validate_prompt_text(spec.prompt, keys, "提示词")
    if spec.random_prompt.strip():
        _validate_prompt_text(spec.random_prompt, keys, "随机档提示词")

    _validate_slot_texts(spec)


def _validate_prompt_text(text: str, keys: set[str], name: str) -> None:
    """一段提示词的形状校验：引用都存在、不裸写 `{{Text N}}`、长度与空值。

    `prompt` 与 `random_prompt` 走**同一套**规则 —— 两者都是「整段发出去的话」，
    区别只在用在哪一档。分两套规则的话，作者会撞上「主提示词能写、随机档不行」
    这种说不通的差别。

    随机档提示词允许为空（表示该模板不支持随机档）；非空时按主提示词的全套规则管。
    """
    for ref in sorted(slot_refs(text)):
        if ref not in keys:
            raise BadRequest(
                f"{name}引用了不存在的槽位「{ref}」：删槽位时要把它的引用一并删掉"
            )
    if _TEXT_LITERAL.search(text):
        raise BadRequest(
            f"{name}不要直接写 {{{{Text N}}}}：文本槽位排第几由槽位顺序决定，"
            "写死会在少选一份时错位"
        )
    if len(text) > MAX_PROMPT:
        raise BadRequest(f"{name}不能超过 {MAX_PROMPT} 字（现在 {len(text)}）")
    if not text.strip():
        raise BadRequest(f"{name}不能为空")


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


def inline_slot_ref(text: str, key: str, value: str) -> str:
    """把文本里所有指向 `key` 的槽位引用（含空格与修饰符）换成 `value`。

    用**同一个** `_SLOT_REF` 逐匹配判断 key，而不是另写一份正则：形状只许一处
    定义（这条已经漂移过三次）。替换函数返回值而不是字符串模板 —— 正文里若
    有反斜杠或 `\\1`，交给 `re.sub` 的字符串替换会被当成反向引用解释掉。

    当前只用于发布时把**固定文本槽位**的正文写进提示词。文本正文不再经
    `{{Text N}}` / `textLibraryIds` 传递，也就不需要复用者对该素材有读取权限。
    """
    def repl(match: re.Match[str]) -> str:
        return value if match.group(1) == key else match.group(0)

    return _SLOT_REF.sub(repl, text)


def is_always_filled(slot: InputSlot) -> bool:
    """这个槽位的素材**必定在**吗（因而它的编号必定存在）？

    两条都要：

    - **素材一定在**：必填（`min_count >= 1`）或已固定（`binding == "fixed"`）。
      可选槽位可能没人填 —— 那时它没有编号可指。
    - **参与编号**：只算图片 / 成片（`ref_mode == "mixed"`）。文本与音频是
      附加输入：文本另有一套 `{{Text N}}` 编号空间，音频根本没有编号。

    与前端 `specDraft.ts::isAlwaysFilled` 同一口径。
    """
    return slot.ref_mode == "mixed" and (
        slot.min_count >= 1 or slot.binding == "fixed"
    )


def always_filled_slot_keys(spec: TemplateSpec) -> frozenset[str]:
    """可以在槽位文案里被引用的槽位 key（见 `is_always_filled`）。"""
    return frozenset(slot.key for slot in spec.slots if is_always_filled(slot))


def strip_valid_refs(text: str) -> str:
    """去掉文字里**合法**的槽位引用，剩下的才是「看不懂的花括号」。

    给「这段文字里有没有塞了不该有的占位符」用：合法的引用摘掉之后，
    还剩下 `{{...}}` 就说明作者写了个认不出来的东西 —— 它会原样进提示词。
    """
    return _SLOT_REF.sub("", text)



#: 一整段「默认描述」锚点：⟦e:key⟧…⟦/e⟧。
#: 段里的内容是**会变**的 —— 使用者给那个槽位选了素材，它就换成「选中时文案」；
#: 没选，才用「未选时文案」。所以段里的引用是**条件性**的。
#:
#: 正常提交路径里锚点已经被前端换成槽位引用了（defaultText.anchorsToRefs），
#: 后端收到的正文没有锚点。这里剥掉它只是**兜底**：万一收到带锚点的正文，
#: 别把段里的引用当成正文引用（那正是「必填槽位只写在框选段里」的那种漏网）。
_ANCHOR_SEGMENT_RE = re.compile(r"⟦e:[a-z][a-z0-9_]{0,39}⟧.*?⟦/e⟧", re.S)


def validate_required_slot_refs(spec: TemplateSpec) -> None:
    """「必定有素材」的槽位必须真的被提示词**正文**引用 —— 否则素材白传给供应商。

    只管**必填且使用者自己挑**的槽位（ref_mode=mixed、min_count>=1、binding=user）。
    已固定的槽位不管：素材由作者钉死，使用者不参与挑，点不点名是作者的选择
    （替换用的「原片」就常常不点名）。而提示词里不点名它，模型就不知道那张图是什么。

    **引用必须落在正文里**（框选的默认文本之外）：

    - 正文里出现过 → 永远在（那才是提示词的骨架）；
    - 只落在某个槽位的「未选时文案 / 选中时文案」里 → **不算数**。

    文案段里的引用为什么不算数（2026-11-27 收紧的一条）：

    - **未选时文案**：使用者给这一格挑了素材 → 它被「选中时文案」顶掉；
      使用者（自定义档）或模型（随机档）改写这一格 → 它被换掉，引用随时可以不见。
      而同一格只能二选一 —— 挑了素材就不会用改写后的文案，改了文案就不会用素材。
    - **选中时文案**：均衡 / 随机档压根不传可选素材，那一段根本不会被用到。

    所以「两段文案里都写了」也不行：只要其中一段能被丢掉，就等于没写。
    之前这里放过「未选时文案 ∩ 选中时文案」一条兜底 —— 那正是漏网的那种形态
    （两段都在 → 预检放行 → 使用者改写其中一段，引用当场消失）。
    """
    # 作者写的是 {{Mixed N}}，这里要的是 key 形态的引用 —— 先归一。
    spec = normalize_refs(spec)
    required = [
        slot
        for slot in spec.slots
        if slot.ref_mode == "mixed"
        and slot.min_count >= 1
        and slot.binding != "fixed"
    ]
    if not required:
        return

    def label_of(slot: InputSlot) -> str:
        return slot.label.strip() or slot.key

    # 正文：把「默认描述段」整段去掉 —— 那部分会变成槽位的「未选时文案」，
    # 落在那一格里的引用是**条件性**的，不算正文引用。
    body = _ANCHOR_SEGMENT_RE.sub(" ", spec.prompt)
    body_refs = set(slot_refs(body))
    # 各槽位的文案里出现过哪些引用（报错时要说清「它现在写在哪」）。
    in_texts: dict[str, list[str]] = {}
    for slot in spec.slots:
        for ref in set(slot_refs(slot.empty_text)) | set(slot_refs(slot.filled_text)):
            in_texts.setdefault(ref, []).append(label_of(slot))

    for slot in required:
        if slot.key in body_refs:
            continue
        where = sorted(set(in_texts.get(slot.key, [])))
        if where:
            raise BadRequest(
                f"槽位「{label_of(slot)}」是必填的，但提示词正文里没有引用它 —— "
                f"它只出现在「{'、'.join(where)}」的文案里"
                "（那段话会被另一段顶掉，也会被改写丢弃），这张图就没人引用了。"
                "请把它引用在正文里（不被框选的那部分）。"
            )
        raise BadRequest(
            f"槽位「{label_of(slot)}」是必填的（素材一定在），但提示词里没有引用它 —— "
            "这张图会原样传给供应商，模型却不知道它是什么。请在提示词正文里引用它。"
        )


def validate_optional_slot_refs(spec: TemplateSpec) -> None:
    """可选槽位**只能被框选**（2026-11-27）。

    ## 规则

    1. 正文里**单个引用**（{{slot:x}}，没有修饰符）某个可选槽位的次数
       **恰好一处** —— 那一处就是它的插入点，也就是框选出来的那段。
    2. 可选槽位若写了「未选时文案」，这句话必须**来自正文里的框选段**
       （empty_text_from_anchor）。

    **只管单个引用**：整组引用（{{slot:x*}}）的语义是「列出这一组的编号」，
    第 N 张引用（{{slot:x.2}}）的语义是「这一组的第 N 张」—— 两者都不是插入点，
    不碰兜底文案（发布闸也不要求整组引用写兜底文案）。它们与插入点是两件事，
    不在这里拦。

    ## 为什么

    可选槽位（min_count == 0）在正文里的那个引用就是它的**插入点**：
    使用者填了素材 → 插编号；没填 → 插「未选时文案」。而「未选时文案」按设计
    是**正文里框选出来的那一段**（提交时那段让位成插入点，文字挪进
    empty_text）。

    于是「正文里指向某个可选槽位的引用」必须恰好一处，且就是框选段：

    - **多于一处** → 多出来的那处是「普通引用」（粘贴进来的）。库里两处长得
      一模一样，打开草稿时无从分辨哪一处才是那段默认描述（hydrateAnchors
      只能猜第一个）→ 作者原话被顶掉。
    - **形态不是单个**（整组 / 第 N 张）→ 框选只产生单个引用；这两种形态
      对可选槽位也不成立（可能没人填，那时没有编号可指）。

    ## 未选时文案必须来自框选段

    empty_text_from_anchor 为假（老数据，或作者当年在老界面手填的）→
    这句话在正文里没有对应的那一段 → 打开草稿时还原不了 → 作者原话会被顶掉。
    预检直接红，强制作者在正文里框选一次（消息里带上原来那句，可以照抄）。
    """
    spec = normalize_refs(spec)
    optional = [
        slot
        for slot in spec.slots
        if slot.ref_mode == "mixed"
        and slot.min_count == 0
        and slot.binding != "fixed"
    ]
    if not optional:
        return

    def label_of(slot: InputSlot) -> str:
        return slot.label.strip() or slot.key

    # 一段提示词里，每个槽位被**单个引用**了几次
    #（整组 / 第 N 张引用不是插入点，不算）。
    def single_ref_counts(text: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for match in _SLOT_REF.finditer(text):
            key, modifier = match.group(1), match.group(2) or ""
            if modifier:
                continue
            counts[key] = counts.get(key, 0) + 1
        return counts

    # **两段提示词各算各的**：随机档提示词是整段替换主提示词的（另一段话），
    # 它的插入点与主提示词里的互不相干。发布闸看两段（_check_executable），
    # 新规则也要跟上 —— 否则随机档里可以写多处引用，编译时同一句兜底文案
    # 在提示词里出现多次。
    sections = (
        ("提示词", single_ref_counts(spec.prompt)),
        ("随机档提示词", single_ref_counts(spec.random_prompt)),
    )

    for slot in optional:
        name = label_of(slot)
        for where, counts in sections:
            hits = counts.get(slot.key, 0)
            if hits > 1:
                raise BadRequest(
                    f"槽位「{name}」是可选的，{where}里却有 {hits} 处单个引用它 —— "
                    "可选槽位只能被引用一处（那一处是它的插入点）：请删掉多余的引用"
                )
        if slot.empty_text.strip() and not slot.empty_text_from_anchor:
            quoted = slot.empty_text.strip()[:60]
            raise BadRequest(
                f"槽位「{name}」的未选时文案「{quoted}」没有对应的正文段落 —— "
                "请在提示词里框选一段作为它的默认描述（可以照抄这句）；"
                "如果这一格本来就没被提示词引用，这句话用不上，删掉即可"
            )


def _validate_slot_texts(spec: TemplateSpec) -> None:
    """校验「选中时文案」与「未选时文案」。

    两条约束各管一件事：

    1. **`filled_text` 可以引用自己，也可以引用「必定有素材」的槽位**
       （必填 / 已固定，见 `is_always_filled`）。自引用（`{{slot:key}}`）
       是**推荐的写法** —— 「人物参考 {{slot:hero}}」里的那个编号就靠它派生成。

       引用**别的**槽位时，展开结果取决于那个槽位排在前面还是后面：编号按
       槽位顺序算，同一段文案在不同填充下会编译出不同结果。所以只放开
       **必定有素材**的那一类 —— 那种槽位的编号永远存在，展开结果稳定；
       可选槽位可能没人填，那时就真的没有编号可指。

    2. **`empty_text` 同样可以引用「必定有素材」的槽位**，但**不能引用自己**。

       原先这里一律禁止，理由是「没填素材时没有编号可指」—— 但那句话只对
       **这一格自己**成立：文字里提的**别的**槽位若是必填 / 已固定，它的
       编号一直存在，引用它完全成立。拿「必填插槽的编号」说事，是把两件事
       混成一件，于是「这段话里提一下必填槽位的素材」这种写法被一起禁掉了。

       引用自己仍然禁止：这段话用的正是「使用者没给这一格挑素材」的时候，
       这一格自己那时没有素材、没有编号。

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
    always_filled = always_filled_slot_keys(spec)
    for slot in spec.slots:
        label = slot.label.strip() or slot.key
        if slot.min_count >= 1 and (slot.filled_text.strip() or slot.empty_text.strip()):
            raise BadRequest(
                f"槽位「{label}」是必填的（最少数 {slot.min_count}），"
                "不能写「选中时文案 / 未选时文案」："
                "必填槽位一定有素材，提示词里直接插入素材编号。"
                "要让素材可以被省略，请把它改成「可选」。"
            )
        if slot.filled_text:
            for ref in sorted(slot_refs(slot.filled_text)):
                if ref != slot.key and ref not in always_filled:
                    raise BadRequest(
                        f"槽位「{label}」的选中时文案引用了「{ref}」："
                        "它只能引用自己，或引用必定有素材的槽位（必填 / 已固定）—— "
                        "可选槽位可能没人填，那时没有编号可指"
                    )
            if has_bad_slot_ref(slot.filled_text):
                raise BadRequest(
                    f"槽位「{label}」的选中时文案里有无法解析的槽位引用"
                    "（检查括号与槽位 key）"
                )
        if slot.empty_text:
            # 未选时文案**不能引用自己**：它用的正是「这一格没素材」的时候。
            refs = [
                ref
                for ref in sorted(slot_refs(slot.empty_text))
                if ref != slot.key and ref not in always_filled
            ]
            if refs:
                raise BadRequest(
                    f"槽位「{label}」的未选时文案引用了「{'、'.join(refs)}」："
                    "只能引用必定有素材的槽位（必填 / 已固定）—— "
                    "可选槽位可能没人填，那时没有编号可指"
                )
            if slot.key in slot_refs(slot.empty_text):
                raise BadRequest(
                    f"槽位「{label}」的未选时文案不能引用它自己："
                    "这段话用的正是「使用者没给这一格挑素材」的时候"
                )
            if has_bad_slot_ref(slot.empty_text):
                raise BadRequest(
                    f"槽位「{label}」的未选时文案里有无法解析的槽位引用"
                    "（检查括号与槽位 key）"
                )


def _validate_groups(spec: TemplateSpec) -> None:
    """封装容器的两条结构约束。

    1. **容器 key 形状**：与槽位 key 同一套（会出现在前端的状态与 DOM 里）。
    2. **成员必须相邻**：容器的顺序由「第一个成员的位置」决定，中间的成员被
       拖出去之后，容器在界面上会断开成两截 —— 而 `{{Mixed N}}` 仍按 `position`
       排，作者看到的顺序与实际编号不一致。所以宁可当场拒，也不让界面出现
       「看起来是两段、编号却连着」的结构。

    其它约束（成员必须来自包、只能是图片/视频）在 `_validate_slot` 里逐条判。
    """
    ordered = sorted(spec.slots, key=lambda item: (item.position, item.key))
    runs: dict[str, list[int]] = {}
    for index, slot in enumerate(ordered):
        if not slot.group:
            continue
        if not _KEY.match(slot.group):
            raise BadRequest(f"封装容器的 key 不合法：{slot.group}")
        runs.setdefault(slot.group, []).append(index)
    for group_key, indexes in runs.items():
        if indexes != list(range(indexes[0], indexes[-1] + 1)):
            names = "、".join(ordered[index].key for index in indexes)
            raise BadRequest(
                f"封装容器「{group_key}」里的槽位必须挨在一起（现在被别的槽位隔开了）："
                f"{names}"
            )


def _validate_slot(slot: InputSlot) -> None:
    if slot.type not in SLOT_TYPES:
        raise BadRequest(f"槽位「{slot.key}」的类型无效：{slot.type}")
    # 音频与文本都不该占视觉编号：执行器按位置取图/视频，塞进去会让编号整体偏移。
    if slot.type == "audio" and slot.ref_mode != "attachment":
        raise BadRequest(f"音频槽位「{slot.key}」必须是附加输入（refMode=attachment）")
    if slot.type == "text" and slot.ref_mode != "attachment":
        raise BadRequest(f"文本槽位「{slot.key}」必须是附加输入（refMode=attachment）")

    # 在封装容器里 = 只能从封装包取，而包只对**用户填的图片/视频**有意义：
    # - 固定槽位是作者钉死的一张，没有「从哪挑」这回事（要钉就钉那张素材）；
    # - 文本/音频各有各的选择器，不参与 `{{Mixed N}}` 分发，包里没有它们的位置。
    if slot.group:
        if slot.binding != "user":
            raise BadRequest(
                f"槽位「{slot.key}」在封装容器里，只能是「用户填」："
                "固定槽位钉的是一张具体素材，放进容器等于把那句话删掉"
            )
        if slot.type not in PACK_GROUP_TYPES:
            raise BadRequest(
                f"槽位「{slot.key}」是{slot.type}，不能放进封装容器："
                "只有图片/视频参与编号分发，包里没有文本/音频的位置"
            )

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
