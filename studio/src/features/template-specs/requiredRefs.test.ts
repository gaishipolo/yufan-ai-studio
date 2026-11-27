import {describe, expect, it} from 'vitest';
import type {SpecSlotIn} from '@/shared/api/schema';
import {bodyText} from './defaultText';
import {requiredSlotRefError} from './requiredRefs';

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

/** 正面图必填、角色可选 —— 与后端守卫测试里的同一套结构。 */
const SLOTS: SpecSlotIn[] = [
  slot('image_1', {label: '正面图', minCount: 1, position: 0}),
  slot('image_2', {label: '角色', minCount: 0, position: 1}),
];

describe('必填槽位必须在正文里被引用', () => {
  it('正文里引用了 → 没问题', () => {
    expect(
      requiredSlotRefError('主图 {{Mixed 1}}，场景 {{Mixed 2}}', SLOTS),
    ).toBeNull();
  });

  it('引用只在框选的默认描述段里 → 报出来（那段会被顶掉 / 改写丢弃）', () => {
    const prompt = '⟦e:image_2⟧主图 {{Mixed 1}}SS⟦/e⟧';
    const error = requiredSlotRefError(prompt, SLOTS);
    expect(error).toContain('正面图');
    expect(error).toContain('正文');
  });

  it('正文里有、段里也有 → 没问题（正文那份才是永远在的）', () => {
    const prompt = '主图 {{Mixed 1}}，⟦e:image_2⟧主图 {{Mixed 1}}SS⟦/e⟧';
    expect(requiredSlotRefError(prompt, SLOTS)).toBeNull();
  });

  it('槽位改成可选 → 不再要求引用', () => {
    const slots = SLOTS.map(
      (item): SpecSlotIn =>
        item.key === 'image_1' ? {...item, minCount: 0} : item,
    );
    expect(requiredSlotRefError('场景 {{Mixed 2}}', slots)).toBeNull();
  });

  it('已固定的槽位不要求引用（素材由作者钉死，点不点名是作者的选择）', () => {
    const slots = SLOTS.map(
      (item): SpecSlotIn =>
        item.key === 'image_1' ? {...item, binding: 'fixed'} : item,
    );
    expect(requiredSlotRefError('场景 {{Mixed 2}}', slots)).toBeNull();
  });

  it('附加输入（文本 / 音频）不参与编号 → 不要求引用', () => {
    const slots = SLOTS.map(
      (item): SpecSlotIn =>
        item.key === 'image_1' ? {...item, refMode: 'attachment'} : item,
    );
    expect(requiredSlotRefError('场景 {{Mixed 2}}', slots)).toBeNull();
  });

  it('正文 = 提示词去掉框选段的内容（与后端看到的同一份）', () => {
    expect(bodyText('前⟦e:image_2⟧段里⟦/e⟧后')).toBe('前后');
    expect(bodyText('没有框选段')).toBe('没有框选段');
  });
});
