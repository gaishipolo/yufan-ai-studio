import { describe, expect, it } from 'vitest';
import { pickWithin } from './slotPick';

/**
 * 点一张素材的四种结果。这块最容易被"顺手改坏"：把单选写成累加，用户会在
 * 一个只能放一张的槽位里点上三张，而提交前才发现。
 */
describe('槽位里点素材', () => {
  it('单选槽位：点新的替换旧的，不累加', () => {
    expect(pickWithin([], 'a', 1)).toEqual(['a']);
    expect(pickWithin(['a'], 'b', 1)).toEqual(['b']);
  });

  it('已选的点一下就是取消', () => {
    expect(pickWithin(['a', 'b'], 'a', 3)).toEqual(['b']);
    expect(pickWithin(['a'], 'a', 1)).toEqual([]);
  });

  it('多选槽位：没满就追加', () => {
    expect(pickWithin([], 'a', 3)).toEqual(['a']);
    expect(pickWithin(['a'], 'b', 3)).toEqual(['a', 'b']);
  });

  it('多选槽位满了就不再变 —— 但同一份数组返回，调用方据此禁用其余图块', () => {
    const full = ['a', 'b'];
    expect(pickWithin(full, 'c', 2)).toBe(full);
  });
});
