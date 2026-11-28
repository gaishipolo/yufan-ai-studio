# 槽位文案允许引用「必填 / 固定」槽位 —— 设计与实现说明

2026-11-26 · 模板封装者反馈触发 · 用户已拍板 · 本轮只做了机制核实与一处纯重构

本文件记的是**本轮已核实的机制事实**与**已拍板的规则**，供后续会话直接照做，不必重读全链路代码。

> **2026-11-28 注**：改写闸门已迁到 `modules/template_pack/slot_text_overrides.py::apply_slot_text_overrides`；
> `random_prompt` 已不参与归一 / 校验 / 编译。本文其余行号是 2026-11-26 的代码快照。

## 1. 反馈与根因

模板封装者的反馈要点：

- 现象：「默认描述里不能带素材引用（没填素材时没有编号可指）；请重新框一段纯文字」这个机制，挡掉了一种合法写法 —— 默认描述那段文字里**引用了别的槽位的素材**（如必填插槽「正面图」「三视图」）。
- 根因：老规则的理由「没填素材时没有编号可指」**只对「自己」成立**。未选时文案用在「使用者没给这一格挑素材」的时候，所以**这一格自己**没有编号；但这段文字里提的**别的**槽位（必填 / 固定）素材一定在，编号一定存在。把两件事混成一件，合法写法被一起禁掉了。

## 2. 用户已拍板（2026-11-26 问卷）

- 问「允许引用哪些槽位」→ 答：**只允许引用必填 / 固定槽位**（编译期必定有素材 → 必定有编号；可选槽位可能没人填，那时确实无编号可指，仍拒）。
- 问「同步到哪些入口」→ 答：**作者侧默认描述 + 使用者改写 + 「选中时文案」**（三处同一套规则，避免口径漂移）。

由此得到新规则：

- 槽位文案（filled_text / empty_text / 使用者改写）可引用**必定有素材的图片 / 成片槽位**：必填（min_count ≥ 1）或固定（binding = fixed）。
- 仍禁止引用**可选（用户填）槽位**。
- 三条细节：
  1. 「选中时文案」仍可引用**自己**（那一支这一格有素材，编号存在）；「未选时文案」不可引用自己（那一支它恰恰没有素材）。
  2. 槽位文案里的引用一律展开成**编号**，不展开成对方的文案段（段落里出现「对方的选中时文案」是另一种东西，且会递归）。
  3. 只算图片 / 成片（ref_mode = mixed）：文本与音频是附加输入，不参与素材编号（音频连引用都不允许）。

## 3. 已核实的代码事实（行号为本轮读取时的位置）

后端 · api/src/core/template_spec/rules.py

- _MIXED_REF 43：作者写法 {{Mixed N}}（N = 第几个图片/成片槽位）。
- _SLOT_REF 54：内部写法 {{slot:key}} / {{slot:key*}} / {{slot:key.N}}。
- _LEFTOVER 67：替换后残留的 {{slot: 判废。
- visual_slot_keys 77：参与 Mixed 编号的槽位（ref_mode = mixed），按 position 排。
- normalize_refs 87→115：规格级换算（prompt / random_prompt / 各槽位两段文案）。
- **normalize_text_refs 87（本轮新增）**：单段文字的换算，供「使用者改写」用。
- has_bad_slot_ref 222：无法解析的引用（只认 {{slot: 形状）。
- slot_refs 235：文字里引用了哪些槽位 key。
- _validate_slot_texts 278：两条旧约束 + 第三条（必填不许带两段文案）。

后端 · api/src/core/template_spec/compile.py

- compile_spec 50：① 先按槽位顺序算 tokens（74-118，编号在此派生）；② 再用 _expander 展开提示词里的引用。
- _expander 152：单遍 re.sub。单张引用 → 段落 / 编号；整组 → 编号列表；第 N 张 → owned[N-1]；没素材 → _fallback_or_raise。
- _fallback_or_raise 244：取 empty_text 当兜底（当前**原样插入，不展开**）。
- _expand_filled 265：展开 filled_text 里的引用（当前**只允许引用自己**）。

后端 · api/src/modules/template_pack/spec_service.py

- to_core 80：入参 → core 词汇。
- _apply_slot_text_overrides 943：使用者改写「未选时文案」；当前第 1002 行「含 {{ 一律拒」。
- publish 670 / _inline_fixed_text 554：发布时把**固定文本**槽位正文抄进提示词，并把该槽位行从快照移除。

后端 · api/src/modules/template_pack/spec_schema.py

- SpecSlotIn 59：filled_text 83-91 / empty_text 92-96。字段说明用 # 注释，**不进 OpenAPI 契约**（api/docs/openapi.json 里搜不到「不许含槽位引用」字样）→ 改注释不必重导契约。

前端 · studio/src/features/template-specs/

- defaultText.ts 241：defaultTextHasRef(text)，见引用就拒（两处调用）。
- SpecWorkspace.tsx 976：段内再编辑时的守卫。
- SpecWorkspace.tsx 1036：框选指派默认描述的守卫。
- RichPromptEditor.tsx 330：段内不弹 @ 候选。
- RichPromptEditor.tsx 423 paint：锚点段内的 token **当纯文字**处理。
- RichPromptEditor.tsx 85 / 106：serialize 与 lengthOf（token 长度按 token 算）。
- RichPromptEditor.tsx 482：highlight，锚点段 = 可编辑的普通文字。
- SlotCard.tsx 342：「选中时文案」候选 = selfRefChips（只列自己）。
- specDraft.ts 590：visualSlots（mixed，按 position）。
- specDraft.ts 612-647：Mixed ↔ slot:key 互转（toSlotTokens / toMixedTokens）。
- specDraft.ts 674：referenceChips，正文可引用的候选。
- specDraft.ts 786-839：selfRefChips，某槽位自己的三种写法。
- specDraft.ts 856：slotRefPattern（与后端同形状，容空格）。
- specDraft.ts 880：promptRefError，正文里引用的形状校验。
- MentionTextarea.tsx 22：MentionCandidate。PromptBox.tsx 12：PromptChip。
- template-library/generateConfirm.ts 83：兜底文案在确认框里的展示。
- template-library/SpecLibrary.tsx 641：fallback 取 slot.emptyText 原样。

数据形态（重要）

- empty_text / filled_text 在库里存的是**作者写法** {{Mixed N}}；后端在 validate_spec / compile_spec 入口换算成内部写法。
- 使用者改写路径：先替换 empty_text（作者写法），再进 compile_spec → normalize_refs → validate_spec，所以校验口径与作者侧天然一致。
- 前端提示词正文也是作者写法（toMixedTokens）。

## 4. 实现计划（照做即可）

后端

1. rules.py：
   - 新增「必定有素材」判据（按 slot 判定：ref_mode = mixed 且（min_count ≥ 1 或 binding = fixed）），供校验与编译共用。
   - _validate_slot_texts：把「只能引用自己 / 不许任何引用」两条换成新规则，报错说清「只能引用必填或已固定的槽位」。
   - normalize_text_refs（本轮已加）供「使用者改写」那段字单独换算。
2. compile.py：
   - 槽位文案里的引用要**展开成编号**：empty_text 兜底插入前展开；filled_text 展开时允许指向必填 / 固定槽位（当前只允许引用自己）。
   - 展开要分清三种修饰符（单张 / 整组 / 第 N 张），与正文一致。
   - 别把「引用别的槽位的编号」和「引用别的槽位的文案段」混起来。
3. spec_service.py：_apply_slot_text_overrides 把「含 {{ 一律拒」换成「只允许引用必填 / 固定槽位」，与作者侧同一套判据。
4. models.py / spec_schema.py 的字段说明同步改口径。

前端

5. specDraft.ts：加「必定有素材」判据 + 「这段文字里的引用是否合法」判定（与后端同口径）。
6. defaultText.ts：defaultTextHasRef 换成「引用了不可引用的槽位」判定。
7. SpecWorkspace.tsx：两处守卫（976 / 1036）改文案与判定。
8. RichPromptEditor.tsx：段内 @ 候选从「不弹」改为「只列必填 / 固定槽位」；段内 token 仍是纯文字（保持现状，别扩大改动面）。
9. SlotCard.tsx：「选中时文案」候选补上必填 / 固定槽位。
10. MentionTextarea / PromptBox 的候选类型要能区分「能不能被槽位文案引用」。

测试（守卫）

11. api/tests/test_template_spec_compile.py：两条旧断言改口径（test_filled_text_cannot_reference_another_slot / test_empty_text_cannot_reference_any_slot —— 现在允许指向必填 / 固定槽位）；新增「引用必填槽位 → 编译出正确编号」「引用可选槽位 → 400」。
12. api/tests/test_template_spec_api.py：test_compile_override_rejects_empty_and_placeholder_text 里的 {{slot:source}}（source 是 fixed → 现在合法）→ 该用例要换目标。
13. studio/src/features/template-specs/defaultText.test.ts 与 SpecWorkspace.test.tsx 同步。

文档 / 契约

14. docs/TEMPLATE_SPEC.md：§6.1 字段表、§6.2 引用规则、§6.3 使用者改写、§12 不变量 12 / 15。
15. 记忆：.workbuddy/memory/modules/template.md（口径）+ 当日日志。
16. 契约：只改 spec_schema.py 的 # 注释 → 不必重导；改了带 description 的字段 → 必须 cd api && app export-openapi，四份生成物一起提交。

## 5. 本轮已发生的改动（工作区，未提交）

- api/src/core/template_spec/rules.py：新增 normalize_text_refs（单段文字的 {{Mixed N}} → {{slot:key}}，越界编号当场报错）；normalize_refs 内部改为复用它（原先是局部 fix 闭包）。这两处是**纯重构，行为不变**；新规则本身还没写。
- 工作区其余：只有 .codebuddy/ 与 .mavis/ 两个未跟踪目录，以及并行的 docs/LLM_CAPABILITY_DESIGN_2026-11-26.md（另一条线，文件不重叠）。
- 基线提交：27ae94f2。

## 6. 工作纪律（沿用本项目口径）

- 不跑全量测试 / CI（除非用户单独要求）；用 api/scripts/select_tests.py 按改动范围定级定向用例。
- 每次改动照跑轻量公共面：test_architecture + test_internal_models + test_openapi_contract + ruff check src tests + mypy --platform win32 src。
- 判别力自检照做（还原改动后守卫必须变红）。
- 推远端前必须征求同意；可本地提交。