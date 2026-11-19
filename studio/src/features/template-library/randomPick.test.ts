import {describe, expect, it} from 'vitest';
import {seededRandom} from './randomPick';

/**
 * 可复现随机源。
 *
 * 随机档抽文案用它：同一颗种子必须给出同一串值（渲染不跳变），不同种子
 * 给出不同串（用户再生成才真的换）。分布均匀性不在这里测（那是 mulberry32
 * 的实现细节），这里钉的是「可复现」这个界面行为依赖的性质。
 */
describe('seededRandom', () => {
  it('同一颗种子 → 同一串值', () => {
    const a = seededRandom(42);
    const b = seededRandom(42);
    const seqA = [a(), a(), a(), a()];
    const seqB = [b(), b(), b(), b()];
    expect(seqA).toEqual(seqB);
  });

  it('不同种子 → 不同的串', () => {
    const a = seededRandom(1);
    const b = seededRandom(2);
    expect(a()).not.toBe(b());
  });

  it('值域在 [0,1)', () => {
    const rand = seededRandom(7);
    for (let i = 0; i < 100; i += 1) {
      const value = rand();
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThan(1);
    }
  });
});
