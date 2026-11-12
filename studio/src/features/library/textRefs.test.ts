import { describe, expect, it } from 'vitest';
import type { LibraryAssetOut } from '@/shared/api/schema';
import { hasForeignMustache, retargetTextTokens } from './textRefs';

/**
 * `retargetTextTokens` 的三条口径：
 *
 * 1. **按 id 重编号**：删除中间一项时，它后面的引用整体前移一位，
 *    不出现「{{Text 3}} 却只剩 2 份绑定」这种越界；
 * 2. **原地保留**：解绑一份、同时在同一位绑上新的，token 留在原位指向新的，
 *    不被摘掉再补到句尾；
 * 3. **新增追加**：新绑定的素材若没有对应编号，编号追加到末尾。
 */

function assetOf(id: string): LibraryAssetOut {
  return {
    id,
    name: `脚本${id}.md`,
    kind: 'text',
    origin: 'upload',
    ownerId: 'u1',
    visibility: 'personal',
    state: 'active',
    createdAt: '2026-11-01T00:00:00Z',
    textVersion: 1,
  };
}

const A = assetOf('a');
const B = assetOf('b');
const C = assetOf('c');

describe('retargetTextTokens · 删除后重编号', () => {
  it('删除中间一项时，后面的引用整体前移一位', () => {
    const result = retargetTextTokens(
      '用 {{Text 1}} 和 {{Text 2}} 和 {{Text 3}}',
      [A, B, C],
      [A, C],
    );
    // B 被删 → 它的 token 消失；C 前移到第 2 位
    expect(result.text).toBe('用 {{Text 1}} 和  和 {{Text 2}}');
    expect(result.dropped).toBe(1);
  });

  it('删除第一项时，后面整体前移', () => {
    const result = retargetTextTokens('用 {{Text 1}} 和 {{Text 2}}', [A, B], [B]);
    expect(result.text).toBe('用  和 {{Text 1}}');
    expect(result.dropped).toBe(1);
  });

  it('换序时按 id 找到新位置', () => {
    const result = retargetTextTokens('用 {{Text 1}} 和 {{Text 2}}', [A, B], [B, A]);
    expect(result.text).toBe('用 {{Text 2}} 和 {{Text 1}}');
    expect(result.dropped).toBe(0);
  });
});

describe('retargetTextTokens · 原地保留', () => {
  it('解绑一份、同时在同一位绑上新的：token 留在原位', () => {
    const result = retargetTextTokens('把 {{Text 1}} 的卖点', [A], [B]);
    // 不能变成「把  的卖点 {{Text 1}}」——那与文件头的承诺不符
    expect(result.text).toBe('把 {{Text 1}} 的卖点');
    expect(result.dropped).toBe(0);
  });

  it('解绑一份、在**后面**新增一份：原 token 留在原位，新编号追加到末尾', () => {
    const result = retargetTextTokens('把 {{Text 1}} 的卖点', [A], [B, C]);
    // 第 1 位被新绑的 B 顶上 → 原位保留；C 在第 2 位 → 追加
    expect(result.text).toBe('把 {{Text 1}} 的卖点 {{Text 2}}');
    expect(result.dropped).toBe(0);
  });

  it('换序 + 换人：旧资产移到别处时按 id 重编号，不享受原地保留', () => {
    const result = retargetTextTokens('用 {{Text 1}} 和 {{Text 2}}', [A, B], [B, C]);
    // 第 1 位现在是旧资产 B → A 被删 → 第 1 位的 token 消失
    // 第 2 位现在是新绑的 C → 原位保留
    expect(result.text).toBe('用  和 {{Text 2}}');
    expect(result.dropped).toBe(1);
  });
});

describe('retargetTextTokens · 新增与手写占位符', () => {
  it('首次绑定时把编号追加到末尾', () => {
    const result = retargetTextTokens('画一个场景', [], [A, B]);
    expect(result.text).toBe('画一个场景 {{Text 1}} {{Text 2}}');
    expect(result.dropped).toBe(0);
  });

  it('用户手写的 {{Text 1}} 不重复追加', () => {
    const result = retargetTextTokens('用 {{Text 1}} 写', [], [A]);
    expect(result.text).toBe('用 {{Text 1}} 写');
    expect(result.dropped).toBe(0);
  });

  it('越界的手写占位符被丢掉并计数', () => {
    const result = retargetTextTokens('用 {{Text 5}} 写', [], [A]);
    // 手写的 {{Text 5}} 越界 → 丢掉并计数；新绑定的那份仍要补上自己的编号
    expect(result.text).toBe('用  写 {{Text 1}}');
    expect(result.dropped).toBe(1);
  });
});

describe('hasForeignMustache', () => {
  it('只认 {{Text N}}，别的花括号算外来模板', () => {
    expect(hasForeignMustache('用 {{Text 1}} 写')).toBe(false);
    expect(hasForeignMustache('用 {{Mixed 1}} 写')).toBe(true);
    expect(hasForeignMustache('普通文本')).toBe(false);
  });
});
