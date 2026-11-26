import {describe, expect, it} from 'vitest';
import {
  anchorsToRefs,
  applyDefaultText,
  assignDefaultText,
  defaultTextRefError,
  hydrateAnchors,
  parseAnchors,
  readDefaultText,
  removeAnchor,
  rekeyAnchor,
  replaceAnchorWithRef,
  stripAnchors,
  syncDefaultTexts,
  unwrapAnchor,
} from './defaultText';
import type {SpecSlotIn} from '@/shared/api/schema';

const slot = (key: string, rest: Partial<SpecSlotIn> = {}): SpecSlotIn => ({
  key,
  type: 'image',
  binding: 'user',
  refMode: 'mixed',
  minCount: 0,
  maxCount: 1,
  label: '',
  filledText: '',
  emptyText: '',
  position: 0,
  ...rest,
});

describe('默认描述的锚点', () => {
  it('框选一段后，正文里出现锚点包住那段', () => {
    const prompt = '将一只猫放入场景这个场景：一望无际的雪原';
    const next = applyDefaultText(
      prompt,
      'image_1',
      {start: 13, end: 20},
      '一望无际的雪原',
    );
    expect(next).toBe('将一只猫放入场景这个场景：⟦e:image_1⟧一望无际的雪原⟦/e⟧');
  });

  it('剥掉锚点后，正文与框选前逐字相同', () => {
    const prompt = '将一只猫放入场景这个场景：一望无际的雪原';
    const next = applyDefaultText(prompt, 'image_1', {start: 13, end: 20}, '一望无际的雪原');
    expect(stripAnchors(next)).toBe(prompt);
  });

  it('锚点不被当成槽位引用（编译器的残留检查不会命中它）', () => {
    const next = applyDefaultText('正文', 'image_1', {start: 0, end: 2}, '正文');
    expect(next).not.toMatch(/\{\{\s*slot:/);
    expect(next).not.toMatch(/\{\{\s*Mixed/);
  });

  it('能读回那段文字', () => {
    const next = applyDefaultText('前缀：一望无际的雪原', 'image_1', {start: 3, end: 10}, '一望无际的雪原');
    expect(readDefaultText(next, 'image_1')).toBe('一望无际的雪原');
  });

  it('锚点位置是内容范围，不含标记本身', () => {
    const next = applyDefaultText('AB', 'k', {start: 0, end: 2}, 'AB');
    const spans = parseAnchors(next);
    expect(spans).toHaveLength(1);
    expect(spans[0].slotKey).toBe('k');
    expect(next.slice(spans[0].start, spans[0].end)).toBe('AB');
  });

  it('同一段文字出现两次时，按锚点定位到指定的那一遍', () => {
    // 两次同样的内容；锚点只包住第二遍。
    const prompt = '雪原。雪原。';
    const next = applyDefaultText(prompt, 'image_1', {start: 3, end: 6}, '雪原');
    expect(readDefaultText(next, 'image_1')).toBe('雪原');
    expect(next).toBe('雪原。⟦e:image_1⟧雪原⟦/e⟧');
  });
});

describe('默认描述的摘除与同步', () => {
  it('摘掉锚点时连它包住的内容一起摘掉', () => {
    const next = applyDefaultText('前缀：一望无际的雪原', 'image_1', {start: 3, end: 10}, '一望无际的雪原');
    expect(removeAnchor(next, 'image_1')).toBe('前缀：');
  });

  it('正文改字后，槽位的未选时文案跟着变', () => {
    const next = applyDefaultText('雪原', 'image_1', {start: 0, end: 2}, '雪原');
    const slots = [{key: 'image_1', emptyText: '雪原'}];
    const edited = next.replace('雪原', '一望无际的雪原');
    const synced = syncDefaultTexts(edited, slots);
    expect(synced[0].emptyText).toBe('一望无际的雪原');
  });

  it('锚点被作者删掉时，该槽位的未选时文案清空（不留死数据）', () => {
    const next = applyDefaultText('前缀：雪原', 'image_1', {start: 3, end: 5}, '雪原');
    const slots = [{key: 'image_1', emptyText: '雪原'}];
    // 关键：把「删之前」的正文传进去，才知道这个槽位曾经有锚点。
    const synced = syncDefaultTexts(stripAnchors(next), slots, next);
    expect(synced[0].emptyText).toBe('');
  });

  it('存量模板的 emptyText 没有锚点，改正文也不许清掉', () => {
    // 老封装界面手填的兜底文案：正文里从来没有过高亮段。
    const slots = [{key: 'image_1', emptyText: ''}, {key: 'image_2', emptyText: '别的'}];
    const synced = syncDefaultTexts('正文改了一句话。', slots, '正文');
    expect(synced[0].emptyText).toBe('');
    expect(synced[1].emptyText).toBe('别的');
  });

  it('没闭合的锚点当作没有（作者正在打字）', () => {
    expect(parseAnchors('正文 ⟦e:image_1⟧半截')).toHaveLength(0);
    expect(readDefaultText('正文 ⟦e:image_1⟧半截', 'image_1')).toBe('');
  });

  it('同一槽位设两次时，旧的那段被替换而不是并存', () => {
    const first = applyDefaultText('AB', 'image_1', {start: 0, end: 2}, 'AB');
    // 第二次：先摘旧的，再算新的选区（摘掉后正文变成空串）。
    const cleared = removeAnchor(first, 'image_1');
    const second = applyDefaultText(cleared, 'image_1', {start: 0, end: 2}, 'XY');
    expect(parseAnchors(second)).toHaveLength(1);
    expect(readDefaultText(second, 'image_1')).toBe('XY');
    expect(stripAnchors(second)).toBe('XY');
  });

  it('重新框选同一段：正文不碎，描述逐字不变', () => {
    const authored = '前缀：⟦e:image_1⟧雪原⟦/e⟧后缀';
    const open = '⟦e:image_1⟧'.length;
    // 编辑器给的选区是「带标记」坐标：跳过开始标记，正好框住「雪原」。
    const start = '前缀：'.length + open;
    const next = assignDefaultText(authored, 'image_1', {start, end: start + 2});
    expect(next).toBe(authored);
    expect(readDefaultText(next, 'image_1')).toBe('雪原');
  });

  it('换一段：旧文字留在正文，锚点移到新框选的那段', () => {
    const authored = '前缀：⟦e:image_1⟧雪原⟦/e⟧后缀';
    // 「后缀」在带标记的坐标系里位于结束标记之后：3 + 11 + 2 + 4 = 20。
    const next = assignDefaultText(authored, 'image_1', {start: 20, end: 22});
    expect(next).toBe('前缀：雪原⟦e:image_1⟧后缀⟦/e⟧');
    expect(readDefaultText(next, 'image_1')).toBe('后缀');
    // 作者写的字一个没丢
    expect(stripAnchors(next)).toBe(stripAnchors(authored));
  });

  it('默认描述里可以引用必定有素材的槽位（必填 / 已固定）', () => {
    // 模板封装者反馈的场景：默认描述那段话里提到别的必填插槽的素材。
    const required = slot('detail', {minCount: 1});
    expect(
      defaultTextRefError('参考 {{Mixed 1}} 的场景', [required, slot('hero')]),
    ).toBeNull();
  });

  it('默认描述里不能引用可选槽位：它可能没人填，没有编号可指', () => {
    const other = slot('detail', {minCount: 0});
    expect(
      defaultTextRefError('参考 {{Mixed 1}} 的场景', [other, slot('hero')]),
    ).toMatch(/可能没有素材/);
  });

  it('默认描述里不能引用它自己', () => {
    expect(
      defaultTextRefError('参考 {{Mixed 1}} 的场景', [slot('hero')], 'hero'),
    ).toMatch(/不能引用它自己/);
  });

  it('纯文字没有引用 → 没问题', () => {
    expect(defaultTextRefError('一望无际的雪原', [slot('hero')])).toBeNull();
  });

  it('unwrapAnchor 只摘标记、保留作者写的那段字', () => {
    const next = applyDefaultText('前缀：雪原，后段', 'image_1', {start: 3, end: 5}, '雪原');
    expect(unwrapAnchor(next, 'image_1')).toBe('前缀：雪原，后段');
    expect(unwrapAnchor(next, 'image_2')).toBe(next);
  });

  it('切必填时锚点段变成插入点，素材才会进提示词', () => {
    const next = applyDefaultText('场景：雪原，后续', 'image_1', {start: 3, end: 5}, '雪原');
    expect(replaceAnchorWithRef(next, 'image_1', '{{Mixed 1}}')).toBe(
      '场景：{{Mixed 1}}，后续',
    );
  });

  it('槽位改名时锚点跟着改，描述不丢', () => {
    const next = applyDefaultText('场景：雪原', 'image_1', {start: 3, end: 5}, '雪原');
    const rekeyed = rekeyAnchor(next, 'image_1', 'image_2');
    expect(readDefaultText(rekeyed, 'image_1')).toBe('');
    expect(readDefaultText(rekeyed, 'image_2')).toBe('雪原');
  });
});

describe('提交形态与编辑形态的往返', () => {
  it('提交时锚点段让位给单张引用，描述留在 emptyText', () => {
    const slots = [slot('image_1', {emptyText: '一望无际的雪原'})];
    const authored = '将一只猫放入场景这个场景：⟦e:image_1⟧一望无际的雪原⟦/e⟧';
    expect(anchorsToRefs(authored, slots)).toBe('将一只猫放入场景这个场景：{{Mixed 1}}');
  });

  it('这是真实模板的形态：填素材时描述消失，没填时用 emptyText 兜底', () => {
    const slots = [
      slot('image_1', {emptyText: '一望无际的雪原', filledText: '参考场景 {{Mixed 1}}'}),
    ];
    const submitted = anchorsToRefs(
      '将一只猫放入场景这个场景：⟦e:image_1⟧一望无际的雪原⟦/e⟧',
      slots,
    );
    // 与库里 spec-g5mpuh 的 prompt 逐字相同 —— 后端编译语义完全没变。
    expect(submitted).toBe('将一只猫放入场景这个场景：{{Mixed 1}}');
  });

  it('载入时按 emptyText + 单张引用把高亮段还原出来', () => {
    const slots = [slot('image_1', {emptyText: '一望无际的雪原'})];
    const saved = '将一只猫放入场景这个场景：{{Mixed 1}}';
    expect(hydrateAnchors(saved, slots)).toBe(
      '将一只猫放入场景这个场景：⟦e:image_1⟧一望无际的雪原⟦/e⟧',
    );
  });

  it('没有兜底文案 / 没有插入点时不会凭空生成锚点', () => {
    const noText = [slot('image_1', {emptyText: ''})];
    expect(hydrateAnchors('场景：{{Mixed 1}}', noText)).toBe('场景：{{Mixed 1}}');
    const noRef = [slot('image_1', {emptyText: '雪原'})];
    expect(hydrateAnchors('场景：空', noRef)).toBe('场景：空');
  });

  it('文本槽位用 key 形态引用；整组引用不当作默认描述的插入点', () => {
    const text = slot('text_1', {type: 'text', refMode: 'attachment', emptyText: '一段文案'});
    expect(anchorsToRefs('说明：⟦e:text_1⟧一段文案⟦/e⟧', [text])).toBe('说明：{{slot:text_1}}');
    const group = slot('image_1', {emptyText: '雪原'});
    expect(hydrateAnchors('参考 {{Mixed 1*}}', [group])).toBe('参考 {{Mixed 1*}}');
  });

  it('反向还原再提交，正文逐字回到原样（幂等）', () => {
    const slots = [slot('image_1', {emptyText: '雪原'})];
    const saved = '场景：{{Mixed 1}}，后续文字';
    expect(anchorsToRefs(hydrateAnchors(saved, slots), slots)).toBe(saved);
  });

  it('槽位被删后，孤儿锚点退回纯文字，不留下坏引用', () => {
    const authored = '场景：⟦e:image_1⟧雪原⟦/e⟧';
    expect(anchorsToRefs(authored, [])).toBe('场景：雪原');
  });

  it('必填 / 固定槽位上残留的锚点不会变成引用', () => {
    const required = [slot('image_1', {minCount: 1, emptyText: ''})];
    expect(anchorsToRefs('场景：⟦e:image_1⟧雪原⟦/e⟧', required)).toBe('场景：雪原');
  });
});
