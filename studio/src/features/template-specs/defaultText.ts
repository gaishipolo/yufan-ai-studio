/**
 * 「槽位默认描述」：正文里的一段文字，绑给某个**可选槽位**。
 *
 * ## 这解决什么问题
 *
 * 以前作者要把「场景描述」从正文剪切到槽位的「未选时文案」框里 ——
 * 剪切、粘贴、再手写一遍「选中时文案」。同一个意思维护两遍，
 * 而且漏剪切就会静默丢掉一整段提示词。
 *
 * 现在：正文里**框选**一段 → 设为某槽位的默认描述 →
 * 那段文字原地留在正文里（高亮、仍可编辑），同时成为该槽位的
 * `emptyText`（= 编译期的「未选时文案」）。
 *
 * ## 唯一真相来源
 *
 * **正文是真相**，`emptyText` 是从正文同步出来的快照。
 *
 * 为什么不反过来（字段是真相、正文是镜像）：作者明确要**在正文里二次编辑**
 * 那段文字 —— 字段是真相的话，正文里那段就成了只读镜像，
 * 作者得跳到槽位卡片去改，改完再回来看正文，两处来回切。
 *
 * ## 锚点为什么必须存在
 *
 * 同一段文字在正文里只出现一次时，可以按**内容**找到它；
 * 但正文里出现两次同样的字（「一望无际的雪原」写了两遍）时，
 * 按内容找就歧义 —— 到底哪一遍是这个槽位的默认描述？
 *
 * 所以位置要用**锚点**记住。锚点是正文里的一段标记：
 *
 *     这个场景：⟦e:image_1⟧一望无际的雪原⟦/e⟧
 *
 * 作者看不见它（渲染时高亮包裹的那段文字，锚点本身不显示），
 * 但它在正文里 —— 编辑、复制、粘贴都带着它，位置不会丢。
 *
 * **提交前锚点变成槽位引用**（`anchorsToRefs`）：正文里那一段让位给
 * `{{slot:key}}`，描述文字本身留在槽位的 `emptyText` 里。这样后端按既有规则
 * 展开：填了素材 → 编号（描述消失），没填 → `emptyText`（描述出现）。
 * 这正好是 Q1=A 要的语义，后端与编译规则都不用改。
 *
 * **载入时反向还原**（`hydrateAnchors`）：服务端存的正文是引用形态，
 * 编辑态要把它还原成高亮段 —— 否则作者下次打开就找不到那段可编辑的文字了。
 *
 * ## 锚点的形状
 *
 * 用 `⟦…⟧` 而不是 `{{…}}`：
 * - `{{…}}` 是**引用 token** 的形状，编译器认它 —— 用它装锚点，
 *   编译器会把锚点当成一个坏引用，报「无法解析的槽位引用」；
 * - `⟦…⟧` 编译器完全不认识，正则 `\{\{\s*slot:` 也不会命中它。
 *
 * key 用**槽位的 key**（不是编号）：编号会随槽位增删而变，
 * key 不会 —— 拖动排序、改槽位类型都不影响锚点。
 */

import type {SpecSlotIn} from '@/shared/api/schema';
import {
  isOptionalSlot,
  referencedKeys,
  singleSlotRefCounts,
  slotRefModes,
  slotRefToken,
  slotTextRefError,
  toMixedTokens,
  toSlotTokens,
} from './specDraft';

/** 锚点开始标记：`⟦e:key⟧`。 */
const OPEN_RE = /⟦e:([a-z][a-z0-9_]{0,39})⟧/g;
/** 锚点结束标记：`⟦/e⟧`（不带 key —— 一段只有一个归属）。 */
const CLOSE = '⟦/e⟧';

/** 一段默认描述在正文里的位置（字符偏移，`start` 含、`end` 不含）。 */
export interface AnchorSpan {
  slotKey: string;
  start: number;
  end: number;
}

/**
 * 解析正文里的锚点。
 *
 * **不支持嵌套**（一个锚点里套另一个）—— 那种正文没法用，
 * 遇到就当外层的内容，忽略内层标记。
 *
 * 没闭合的锚点**作废**：作者可能正在打字（刚开始写就触发同步），
 * 这时把它当「没有默认描述」比截半截更安全。
 */
export function parseAnchors(prompt: string): AnchorSpan[] {
  const spans: AnchorSpan[] = [];
  OPEN_RE.lastIndex = 0;
  let match = OPEN_RE.exec(prompt);
  while (match) {
    const key = match[1];
    const contentStart = match.index + match[0].length;
    const closeAt = prompt.indexOf(CLOSE, contentStart);
    if (closeAt < 0) {
      // 没闭合：跳过这个开始标记，继续找下一个。
      OPEN_RE.lastIndex = contentStart;
      match = OPEN_RE.exec(prompt);
      continue;
    }
    spans.push({slotKey: key, start: contentStart, end: closeAt});
    OPEN_RE.lastIndex = closeAt + CLOSE.length;
    match = OPEN_RE.exec(prompt);
  }
  return spans;
}

/**
 * 剥掉锚点标记、**保留内容**（`⟦e:k⟧文字⟦/e⟧` → `文字`）。
 *
 * 用于锚点失配时的兜底清理（例如槽位被删，那段文字要变回普通正文），
 * **不用于提交** —— 提交必须走 `anchorsToRefs`，否则描述不会随素材消失。
 */
export function stripAnchors(prompt: string): string {
  return prompt.replace(/⟦e:[a-z][a-z0-9_]{0,39}⟧/g, '').replaceAll(CLOSE, '');
}

/**
 * 提交形态：锚点段 → 单张槽位引用。
 *
 * 正文里那一段**让位给引用**，描述文字留在槽位的 `emptyText`：
 *
 *     ⟦e:image_1⟧一望无际的雪原⟦/e⟧   →   {{Mixed 1}}
 *
 * 后端按既有规则展开这个引用：填了素材 → 编号（描述消失，Q1=A），
 * 没填 → `emptyText`（描述出现）。所以后端和编译规则一个字都不用改。
 *
 * 槽位已不存在（作者刚删掉）或不再是可选时，锚点段**不要再变成引用**：
 * 前者会留下一条必失败的引用，后者会把正文里一段话凭空吞掉。
 * 这两种情况退回纯文字，内容不丢。
 */
export function anchorsToRefs(prompt: string, slots: SpecSlotIn[]): string {
  const spans = parseAnchors(prompt);
  if (!spans.length) return prompt;
  const byKey = new Map(slots.map(slot => [slot.key, slot]));
  let out = '';
  let cursor = 0;
  for (const span of spans) {
    const openAt = prompt.lastIndexOf(`⟦e:${span.slotKey}⟧`, span.start);
    const closeAt = span.end + CLOSE.length;
    out += prompt.slice(cursor, openAt);
    const slot = byKey.get(span.slotKey);
    if (
      slot &&
      (slot.minCount ?? 1) === 0 &&
      (slot.binding ?? 'user') === 'user'
    ) {
      out += slotRefToken(slots, slot);
    } else {
      out += prompt.slice(span.start, span.end);
    }
    cursor = closeAt;
  }
  return out + prompt.slice(cursor);
}

/**
 * 载入形态：把「有兜底文案 + 有单张引用」的槽位还原成正文里的高亮段。
 *
 * 服务端存的正文是**提交形态**（`{{Mixed N}}` / `{{slot:key}}`），
 * 编辑态必须把那一段重新显示成可编辑的高亮文字 —— 否则作者打开模板时
 * 只看到一枚胶囊，兜底文案躲进槽位卡片里，没法在正文里二次编辑。
 *
 * 只还原**第一个**单张引用：一个槽位只能有一段默认描述，其余引用保持原样
 * （它们在没填素材时仍会展开成同一个 `emptyText`，那是后端既有语义）。
 */
export function hydrateAnchors(prompt: string, slots: SpecSlotIn[]): string {
  let out = toSlotTokens(prompt, slots);
  for (const slot of slots) {
    const text = slot.emptyText?.trim() ?? '';
    /* 只还原**标记过「来自框选段」**的那一格（2026-11-27）。
     *
     * 以前这里是「有未选时文案 + 有单张引用」就还原 —— 于是老模板（未选时文案
     * 是当年在老界面手填的，正文里根本没有对应的那一段）也被还原：程序把正文里
     * 作者自己写的引用换成了那句手填的话 → 作者原话被顶掉。
     *
     * 现在有标记才还原：没标记的老数据**保持引用原样**（作者原话不被顶掉），
     * 预检会要求作者在正文里重新框选一次（强制改成符合要求的）。 */
    if (
      !text ||
      slot.emptyTextFromAnchor !== true ||
      (slot.minCount ?? 1) !== 0 ||
      (slot.binding ?? 'user') !== 'user' ||
      parseAnchors(out).some(item => item.slotKey === slot.key)
    ) {
      continue;
    }
    const escaped = slot.key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const single = new RegExp(`\\{\\{\\s*slot:${escaped}\\s*\\}\\}`, 'g');
    const hit = single.exec(out);
    if (!hit) continue;
    out =
      out.slice(0, hit.index) +
      `⟦e:${slot.key}⟧${text}⟦/e⟧` +
      out.slice(hit.index + hit[0].length);
  }
  // 正文存的始终是**作者写法**（`{{Mixed N}}`）：胶囊标签、renumberRefs、
  // promptRefError 都按这个口径。误存 key 形态会让渲染出的胶囊与校验打架。
  return toMixedTokens(out, slots);
}

/**
 * 在正文上包住一段文字（不含换段的偏移换算）。
 *
 * `selected` 是**当前正文**里的偏移，坐标系与正文一致（可能含别的锚点标记）。
 * 同一个槽位要换段时用 `assignDefaultText`，它负责摘旧段并重算选区。
 */
export function applyDefaultText(
  prompt: string,
  slotKey: string,
  selected: {start: number; end: number},
  content: string,
): string {
  const before = prompt.slice(0, selected.start);
  const after = prompt.slice(selected.end);
  return `${before}⟦e:${slotKey}⟧${content}⟦/e⟧${after}`;
}

/**
 * 把正文里选中的一段设为某槽位的默认描述（**含换段的偏移换算**）。
 *
 * `range` 来自编辑器，坐标系与 `value` 一致 —— 即**带锚点标记**的正文。
 * 同一个槽位只能有一段默认描述：**旧锚点只摘标记、保留那段字**（作者写的
 * 正文不该因为换一次指派就消失），再把新选区包成锚点。
 *
 * 这段换算以前写在组件里，按「旧锚点整段长度平移」处理 —— 重新框选同一段
 * 时会把 `前缀：⟦e:image_1⟧雪原⟦/e⟧后缀` 切成
 * `前缀⟦e:image_1⟧：后⟦/e⟧缀`：选区自己也在旧段里，整段平移越过段首。
 * 正确做法：先把带标记坐标按标记长度换算成纯文字坐标，再包新段。
 */
export function assignDefaultText(
  prompt: string,
  slotKey: string,
  range: {start: number; end: number},
): string {
  const open = `⟦e:${slotKey}⟧`;
  const span = parseAnchors(prompt).find(item => item.slotKey === slotKey);
  if (!span) {
    return applyDefaultText(prompt, slotKey, range, prompt.slice(range.start, range.end));
  }
  const openAt = prompt.lastIndexOf(open, span.start);
  const closeAt = span.end + CLOSE.length;

  /* 带标记坐标 → 纯文字坐标：穿过开始标记减 open.length，
     穿过结束标记再减 CLOSE.length；落在标记内部则贴到相邻边界。 */
  const unmark = (at: number): number => {
    if (at <= openAt) return at;
    if (at < span.start) return openAt;
    if (at <= span.end) return at - open.length;
    if (at < closeAt) return span.start - open.length;
    return at - open.length - CLOSE.length;
  };

  const plain = unwrapAnchor(prompt, slotKey);
  const start = unmark(range.start);
  const end = unmark(range.end);
  return applyDefaultText(plain, slotKey, {start, end}, plain.slice(start, end));
}

/**
 * 这段默认描述里的引用是否合法。
 *
 * **返回 `null` = 没问题**，否则是给作者看的理由。
 *
 * 判据（与后端 `core/template_spec/rules.py::_validate_slot_texts` 同一套）：
 * 默认描述里可以引用**必定有素材**的槽位（必填 / 已固定）—— 那种槽位每次
 * 编译都有素材、都有编号，引用它成立。引用**可选**槽位仍不行：它可能没人填，
 * 那时没有编号可指。
 *
 * 引用**自己**也不行：默认描述用的正是「使用者没给这一格挑素材」的时候，
 * 那一格自己那时没有素材。
 *
 * 老规则是「见引用就拒」，把「提一下必填插槽的素材」这种写法一起误伤了 ——
 * 那正是模板封装者反馈的场景。
 */
export function defaultTextRefError(
  text: string,
  slots: SpecSlotIn[],
  selfKey?: string,
): string | null {
  if (selfKey && referencedKeys(text, slots).includes(selfKey)) {
    return '不能引用它自己（使用者没给这一格挑素材时，它没有编号可指）';
  }
  return slotTextRefError(text, slots, {allowSelf: false, selfKey});
}

/**
 * 提示词里**不是**框选默认描述段的那部分（= 后端眼里的「正文」）。
 *
 * 后端 core/template_spec/rules.py::_ANCHOR_SEGMENT_RE 判「必填槽位是否被
 * 正文引用」时，剥掉的就是这些段 —— 两边要算同一份，所以标记形状只在这里
 * 定义一次（parseAnchors 的锚点形状）。
 *
 * 段**连内容一起**去掉：提交时那段内容会挪进槽位的「未选时文案」，
 * 落在那一格里的引用是条件性的，不算正文引用。
 */
export function bodyText(prompt: string): string {
  const spans = parseAnchors(prompt);
  if (!spans.length) return prompt;
  let out = '';
  let cursor = 0;
  for (const span of spans) {
    const openAt = prompt.lastIndexOf(
      '⟦e:' + span.slotKey + '⟧',
      span.start,
    );
    const closeAt = span.end + CLOSE.length;
    if (openAt < 0) {
      cursor = span.end;
      continue;
    }
    out += prompt.slice(cursor, openAt);
    cursor = closeAt;
  }
  return out + prompt.slice(cursor);
}

/**
 * 正文里指向**可选槽位**的单个引用，必须落在框选段内。
 *
 * 可选槽位在正文里的那个引用就是它的**插入点**：使用者填了素材 → 插编号；
 * 没填 → 插「未选时文案」。而那一处必须是作者框选出来的那一段 —— 库里只存
 * 「一处引用 + 一句文案」，分不出哪一处才是框选段，多出来的一处（粘贴进来的
 * 「普通引用」）会让「打开草稿时还原哪一段」变成猜 → 作者原话被顶掉。
 *
 * **整组引用不算**：它的语义是「列出这一组的编号」，不是插入点，也不碰兜底
 * 文案（发布闸同样不要求整组引用写兜底文案）。
 *
 * 与后端 `core/template_spec/rules.py::validate_optional_slot_refs` 同一口径
 * （那边管库里的形状，这边管作者写的时候）。
 */
export function optionalSlotRefError(
  prompt: string,
  slots: SpecSlotIn[],
): string | null {
  /* 「可选」的判据与后端**同一句**（rules.py::validate_optional_slot_refs）：
     参与编号（refMode=mixed）、用户自己挑（binding!=fixed）、可挑可不填
     （minCount=0）。少了后两个条件就会把文本槽位与已固定槽位一起算进来 ——
     而后端不管它们（它们不是插入点）→ 界面提供的引用按钮被自己拒掉。 */
  const optional = slots.filter(
    slot =>
      (slot.refMode ?? 'mixed') === 'mixed' &&
      (slot.binding ?? 'user') !== 'fixed' &&
      isOptionalSlot(slot),
  );
  if (!optional.length) return null;
  /* 段外的部分 = 作者自己写的引用；段内的 = 框选段（插入点）。 */
  const spans = parseAnchors(prompt);
  let outside = '';
  let cursor = 0;
  for (const span of spans) {
    outside += prompt.slice(cursor, span.start);
    cursor = span.end;
  }
  outside += prompt.slice(cursor);
  /* 作者写法（{{Mixed N}}）先换成内部写法（{{slot:key}}）才认得出指谁。 */
  const modes = slotRefModes(toSlotTokens(outside, slots), slots);
  for (const slot of optional) {
    const forms = modes[slot.key];
    if (!forms || !forms.has('')) continue;
    const name = slot.label?.trim() || slot.key;
    return (
      `「${name}」是可选槽位，只能被框选 —— 正文里引用它的地方必须落在框选段里` +
      '（那一段是它的插入点）；请把别处的引用删掉'
    );
  }
  return null;
}

/**
 * 随机档提示词里的可选槽位：最多只能被**单张引用一次**。
 *
 * 随机档没有「框选段」—— 它的插入点就是那处引用本身。后端
 * `validate_optional_slot_refs` 对两段提示词各算各的：同一可选槽位出现
 * 两处单张引用时，界面上两处长得一模一样，编译期不知道哪一处该换成
 * 「未选时文案」。这里补上前端提示，避免发布才 400。
 */
export function optionalSlotRefCountError(
  prompt: string,
  slots: SpecSlotIn[],
): string | null {
  const optional = slots.filter(
    slot =>
      (slot.refMode ?? 'mixed') === 'mixed' &&
      (slot.binding ?? 'user') !== 'fixed' &&
      isOptionalSlot(slot),
  );
  if (!optional.length) return null;
  const counts = singleSlotRefCounts(prompt, slots);
  for (const slot of optional) {
    if ((counts[slot.key] ?? 0) > 1) {
      const name = slot.label?.trim() || slot.key;
      return `「${name}」是可选槽位，提示词里最多引用一次（那处就是它的插入点）`;
    }
  }
  return null;
}
/** 摘掉某槽位的锚点（连它包住的内容一起 —— 那一段就是默认描述本身）。 */
export function removeAnchor(prompt: string, slotKey: string): string {
  const span = parseAnchors(prompt).find(item => item.slotKey === slotKey);
  if (!span) return prompt;
  // 往回找开始标记。
  const openIndex = prompt.lastIndexOf(`⟦e:${slotKey}⟧`, span.start);
  if (openIndex < 0) return prompt;
  return prompt.slice(0, openIndex) + prompt.slice(span.end + CLOSE.length);
}

/**
 * 摘掉某槽位的锚点标记，**保留包住的那段文字**。
 *
 * 槽位删掉 / 切成必填 / 切成固定时用：那段话是作者写的提示词正文，
 * 不该跟着字段一起消失；只是它不再是一个可替换的默认描述。
 */
export function unwrapAnchor(prompt: string, slotKey: string): string {
  let out = prompt;
  for (const span of parseAnchors(out).filter(item => item.slotKey === slotKey)) {
    const openAt = out.lastIndexOf(`⟦e:${slotKey}⟧`, span.start);
    if (openAt < 0) continue;
    const closeAt = span.end + CLOSE.length;
    out = out.slice(0, openAt) + out.slice(span.start, span.end) + out.slice(closeAt);
  }
  return out;
}

/**
 * 锚点段 → 单张引用（单个槽位版）。
 *
 * 槽位切成必填 / 固定时用：那种槽位一定有素材，编译期直接插编号，
 * 所以那一段必须留成**插入点**，不能变成普通文字（否则素材再也不进提示词）。
 * 原来的描述文字随 `emptyText` 一起被清掉，界面已明确提示作者。
 */
export function replaceAnchorWithRef(
  prompt: string,
  slotKey: string,
  ref: string,
): string {
  const span = parseAnchors(prompt).find(item => item.slotKey === slotKey);
  if (!span) return prompt;
  const openAt = prompt.lastIndexOf(`⟦e:${slotKey}⟧`, span.start);
  if (openAt < 0) return prompt;
  return prompt.slice(0, openAt) + ref + prompt.slice(span.end + CLOSE.length);
}

/**
 * 槽位 key 变了（改类型会重命名）：锚点里的 key 跟着改。
 *
 * 不改的话锚点会变成孤儿 —— 它指着一个已不存在的槽位，
 * 提交时 `anchorsToRefs` 认不出它，正文里那段描述就再也不会被引用。
 */
export function rekeyAnchor(prompt: string, oldKey: string, newKey: string): string {
  return prompt.replaceAll(`⟦e:${oldKey}⟧`, `⟦e:${newKey}⟧`);
}

/**
 * 读出某槽位的默认描述（正文里那段的文字）。
 *
 * 找不到（还没设、或锚点被作者删坏了）返回空串。
 */
export function readDefaultText(prompt: string, slotKey: string): string {
  const span = parseAnchors(prompt).find(item => item.slotKey === slotKey);
  if (!span) return '';
  return prompt.slice(span.start, span.end);
}

/**
 * 正文改了之后，各槽位的默认描述**跟着变**。
 *
 * 这是「正文是真相」的实现：锚点的位置跟着文字一起平移，
 * 内容变了就把新内容写回该槽位的 `emptyText`。
 *
 * **清除只发生在「锚点确实被删掉」时**（`previousPrompt` 里有、新正文里没有）。
 *
 * 不能见「没有锚点」就清：存量模板的 `emptyText` 本来就没有锚点 ——
 * 那是老封装界面里手填的字段，正文里从头到尾没有那段高亮。作者只是改了
 * 一句正文，就把兜底文案清掉，是静默丢数据。区分的办法只能是**前后对比**：
 * 上一版正文里有这个锚点、这一版没了，才是作者亲手删的。
 */
export function syncDefaultTexts<
  T extends {key: string; emptyText?: string; emptyTextFromAnchor?: boolean},
>(prompt: string, slots: T[], previousPrompt = ''): T[] {
  const spans = parseAnchors(prompt);
  const byKey = new Map(spans.map(span => [span.slotKey, span]));
  const had = new Set(parseAnchors(previousPrompt).map(span => span.slotKey));
  return slots.map(slot => {
    const span = byKey.get(slot.key);
    if (!span) {
      /* 段没了 → 这一格不再有「来自框选段」的未选时文案。
         作者亲手删掉的（上一版里有段）连文案一起清；只是从来没框选过
         （老数据）只把标记落回 false。 */
      const cleared =
        had.has(slot.key) && slot.emptyText ? {...slot, emptyText: ''} : {...slot};
      return cleared.emptyTextFromAnchor
        ? {...cleared, emptyTextFromAnchor: false}
        : cleared;
    }
    const text = prompt.slice(span.start, span.end);
    const next =
      text === (slot.emptyText ?? '') ? {...slot} : {...slot, emptyText: text};
    /* 段在 → 标记为 true（标记的真相来源也是正文，与文案同一个写点）。 */
    return next.emptyTextFromAnchor ? next : {...next, emptyTextFromAnchor: true};
  });
}
