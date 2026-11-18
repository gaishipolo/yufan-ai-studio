import {describe, expect, it} from 'vitest';
import {randomPick} from './randomPick';

/**
 * 随机档的洗牌。
 *
 * 判别力自检：**不能只断言「结果里有几张」** —— 那样「每次都取前 N 个」
 * 的假随机也能过。这里的用例都钉住「同样的池子 + 同样的随机源 → 同样的
 * 结果」以及「不同的随机源 → 不同的结果」。
 */

/** 固定序列的随机源：每次调用取下一个值，取完回到第一个。 */
function seeded(values: number[]): () => number {
  let index = 0;
  return () => {
    const value = values[index % values.length];
    index += 1;
    return value;
  };
}

describe('randomPick', () => {
  it('池子空或数量为 0 时返回空', () => {
    expect(randomPick([], 3)).toEqual([]);
    expect(randomPick(['a'], 0)).toEqual([]);
  });

  it('挑出的数量不超过池子大小', () => {
    expect(randomPick(['a', 'b'], 5)).toHaveLength(2);
  });

  it('不重复（同一个 id 不会挑两次）', () => {
    const picked = randomPick(['a', 'b', 'c', 'd'], 3, seeded([0.1, 0.2, 0.3]));
    expect(new Set(picked).size).toBe(picked.length);
  });

  it('同样的池子 + 同样的随机源 → 同样的结果（可复现）', () => {
    const pool = ['a', 'b', 'c', 'd', 'e'];
    const first = randomPick(pool, 3, seeded([0.1, 0.5, 0.9, 0.3]));
    const second = randomPick(pool, 3, seeded([0.1, 0.5, 0.9, 0.3]));
    expect(first).toEqual(second);
  });

  it('不同的随机源 → 不同的结果（不是固定取前 N 个）', () => {
    const pool = ['a', 'b', 'c', 'd', 'e', 'f'];
    const first = randomPick(pool, 3, seeded([0.01, 0.02, 0.03]));
    const second = randomPick(pool, 3, seeded([0.9, 0.8, 0.7]));
    expect(first).not.toEqual(second);
    /* 「取前 N 个」的实现永远返回 ['a','b','c'] —— 这条断言直接把它钉死。 */
    expect(first).not.toEqual(['a', 'b', 'c']);
  });

  it('随机源返回 1 时不越界（夹到 [0,1)）', () => {
    expect(() => randomPick(['a', 'b', 'c'], 2, () => 1)).not.toThrow();
    expect(randomPick(['a', 'b', 'c'], 2, () => 1)).toHaveLength(2);
  });

  it('随机源返回负数时也安全', () => {
    expect(randomPick(['a', 'b', 'c'], 2, () => -1)).toHaveLength(2);
  });

  it('不改动传入的池子', () => {
    const pool = ['a', 'b', 'c'];
    randomPick(pool, 2, seeded([0.3, 0.6]));
    expect(pool).toEqual(['a', 'b', 'c']);
  });
});
