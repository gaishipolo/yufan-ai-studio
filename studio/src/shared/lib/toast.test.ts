import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { toast, useToastStore } from './toast';

/** 每个用例都从空列表开始：store 是模块级单例，残留会互相污染 */
function reset() {
  for (const item of useToastStore.getState().items) {
    useToastStore.getState().dismiss(item.id);
  }
}

const items = () => useToastStore.getState().items;

describe('toast 通用通道', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    reset();
  });

  afterEach(() => {
    reset();
    vi.useRealTimers();
  });

  it('旧写法照旧可用：toast(文案, 色调)', () => {
    toast('已保存');
    toast('失败了', 'error');

    expect(items().map((item) => [item.message, item.tone])).toEqual([
      ['已保存', 'default'],
      ['失败了', 'error'],
    ]);
  });

  it('默认 2.2 秒后自己消失', () => {
    toast('已保存');
    expect(items()).toHaveLength(1);

    vi.advanceTimersByTime(2200);
    expect(items()).toHaveLength(0);
  });

  it('带动作的留久一点，否则来不及点', () => {
    toast({ message: '已完成', action: { label: '查看', onClick: () => {} } });

    vi.advanceTimersByTime(2200);
    expect(items()).toHaveLength(1);

    vi.advanceTimersByTime(2300);
    expect(items()).toHaveLength(0);
  });

  it('同 key 是替换而不是堆叠：连点三次只留一条', () => {
    toast({ message: '已保存', key: 'save' });
    toast({ message: '已保存', key: 'save' });
    toast({ message: '已保存', key: 'save' });

    expect(items()).toHaveLength(1);
    expect(items()[0].message).toBe('已保存');
  });

  it('替换要重置计时，不能沿用旧的那条', () => {
    toast({ message: '第一句', key: 'save', duration: 1000 });
    vi.advanceTimersByTime(800);
    toast({ message: '第二句', key: 'save', duration: 1000 });

    vi.advanceTimersByTime(800); // 距第一条 1600ms，距替换后只有 800ms
    expect(items()).toHaveLength(1);
    expect(items()[0].message).toBe('第二句');

    vi.advanceTimersByTime(300);
    expect(items()).toHaveLength(0);
  });

  it('duration=0 表示驻留，等用户自己关', () => {
    toast({ message: '需要处理', duration: 0, key: 'sticky' });
    vi.advanceTimersByTime(60_000);

    expect(items()).toHaveLength(1);
  });

  it('dismiss 会顺手清掉它的 key，之后同 key 是新的一条', () => {
    toast({ message: '第一条', key: 'k' });
    const first = items()[0].id;
    useToastStore.getState().dismiss(first);

    toast({ message: '第二条', key: 'k' });
    expect(items()).toHaveLength(1);
    expect(items()[0].id).not.toBe(first);
  });
});
