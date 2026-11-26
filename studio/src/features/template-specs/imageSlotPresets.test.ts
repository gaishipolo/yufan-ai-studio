import { beforeEach, describe, expect, it, vi } from 'vitest';
import { toast } from '@/shared/lib/toast';
import {
  DEFAULT_PRESETS,
  filledTextOf,
  loadPresets,
  newPresetId,
  nextPresetLabel,
  savePresets,
  SELF_REF,
} from './imageSlotPresets';

vi.mock('@/shared/lib/toast', () => ({ toast: vi.fn() }));

beforeEach(() => {
  localStorage.clear();
  vi.mocked(toast).mockClear();
});

describe('图片插槽预设 · 占位符替换', () => {
  it('单张引用换成新槽位的 key，名字照抄', () => {
    const preset = {
      id: 'p1',
      name: '场景参考',
      filledText: `空间环境以 ${SELF_REF} 为准`,
    };
    expect(filledTextOf(preset, 'image_3')).toBe('空间环境以 {{slot:image_3}} 为准');
    expect(filledTextOf(preset, 'image_2')).toBe('空间环境以 {{slot:image_2}} 为准');
  });

  /** 槽位恒为单张（maxCount=1），整组引用没有语义 —— 不提供、也不替换。 */
  it('文案里手写的 {{slot:key*}} 原样保留（不做整组替换）', () => {
    const preset = {id: 'p1', name: '产品', filledText: '{{slot:key*}} 不会被改写'};
    expect(filledTextOf(preset, 'image_4')).toBe('{{slot:key*}} 不会被改写');
  });

  it('没有占位符的文案原样保留', () => {
    expect(filledTextOf({id: 'p', name: 'x', filledText: '固定文案'}, 'image_1')).toBe('固定文案');
  });
});

describe('重复插入同一预设 · 槽位名区分', () => {
  const slots = (...labels: string[]) =>
    labels.map((label, index) => ({key: `image_${index + 1}`, label, type: 'image' as const, position: index}));

  it('第一个保持原名，第二个起加最小可用序号', () => {
    expect(nextPresetLabel('角色', [])).toBe('角色');
    expect(nextPresetLabel('角色', slots('角色'))).toBe('角色 2');
    expect(nextPresetLabel('角色', slots('角色', '角色 2'))).toBe('角色 3');
  });

  it('删掉中间一个再插，不与现存的撞名（取最小可用）', () => {
    expect(nextPresetLabel('角色', slots('角色', '角色 3'))).toBe('角色 2');
  });

  it('名字带空格时先收拢再判断', () => {
    expect(nextPresetLabel('  角色  ', slots('角色'))).toBe('角色 2');
  });
});

describe('图片插槽预设 · 本地存储', () => {
  it('首次使用给一组示例预设', () => {
    expect(loadPresets()).toEqual(DEFAULT_PRESETS);
  });

  it('存了再读逐字相同，改过的名称与文案不丢', () => {
    const presets = [{id: 'p1', name: '我的场景', filledText: '参考 {{slot:key}}'}];
    expect(savePresets(presets)).toBe(true);
    expect(loadPresets()).toEqual(presets);
  });

  it('删空后不再把示例灌回来', () => {
    savePresets([]);
    expect(loadPresets()).toEqual([]);
  });

  it('坏 JSON / 结构不对的条目丢掉，不让配置页崩掉', () => {
    localStorage.setItem('sf_image_slot_presets', '{not json');
    expect(loadPresets()).toEqual(DEFAULT_PRESETS);

    localStorage.setItem(
      'sf_image_slot_presets',
      JSON.stringify([{id: 'ok', name: '好', filledText: 'x'}, {id: 3, name: '坏'}]),
    );
    expect(loadPresets()).toEqual([{id: 'ok', name: '好', filledText: 'x'}]);
  });

  it('浏览器禁用存储时给提示，不静默失败', () => {
    /* 替换掉 globalThis.localStorage 这个**属性**，而不是在它的 setItem 上打桩：
       Node 的实验性 localStorage 是个每次读取都可能换对象的代理，
       `vi.spyOn(localStorage, 'setItem')` 打到的不是被测代码拿到的那个。
       这里直接换掉属性，确保 savePresets 读到的就是会抛的那个实现。 */
    const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: {
        getItem: () => null,
        setItem: () => {
          throw new Error('quota');
        },
        removeItem: () => {},
        clear: () => {},
        key: () => null,
        length: 0,
      },
    });
    try {
      expect(savePresets([])).toBe(false);
      expect(toast).toHaveBeenCalledWith(expect.stringContaining('没能保存'), 'error');
    } finally {
      if (original) Object.defineProperty(globalThis, 'localStorage', original);
    }
  });

  it('预设 id 带前缀、彼此不同', () => {
    const ids = new Set(Array.from({length: 20}, () => newPresetId()));
    expect(ids.size).toBe(20);
    expect(Array.from(ids).every((id) => id.startsWith('preset-'))).toBe(true);
  });
});
