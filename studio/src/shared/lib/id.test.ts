import { afterEach, describe, expect, it, vi } from 'vitest';
import { createId } from './id';

/** 标准 v4：版本位是 4，变体位是 8/9/a/b。 */
const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe('createId', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('安全上下文能拿到 randomUUID 时直接用', () => {
    vi.stubGlobal('crypto', { randomUUID: () => 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee' });
    expect(createId()).toBe('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee');
  });

  it('局域网 HTTP 只有 getRandomValues 时仍拼出合法 v4', () => {
    /* 这正是 http://192.168.x.x 打开时的形状：randomUUID 不存在，getRandomValues 在。
       这里留真实的 getRandomValues，顺带验证不会退化成同一个值。 */
    vi.stubGlobal('crypto', { getRandomValues: globalThis.crypto.getRandomValues.bind(globalThis.crypto) });

    const ids = Array.from({ length: 200 }, () => createId());
    for (const id of ids) expect(id).toMatch(V4);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('randomUUID 抛错时降级而不是把异常抛给调用方', () => {
    vi.stubGlobal('crypto', {
      randomUUID: () => {
        throw new TypeError('只能在安全上下文使用');
      },
      getRandomValues: (bytes: Uint8Array) => bytes.fill(7),
    });
    expect(createId()).toMatch(V4);
  });

  it('连 getRandomValues 都没有时用随机兜底，格式不变', () => {
    vi.stubGlobal('crypto', {});
    expect(createId()).toMatch(V4);
  });

  it('弱随机兜底只警告一次，批量上传不会刷屏', async () => {
    /* `warned` 是模块级标记，这里重新加载一份模块才能观察第一次调用。 */
    vi.resetModules();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.stubGlobal('crypto', {});
    const fresh = await import('./id');

    fresh.createId();
    fresh.createId();

    expect(warn).toHaveBeenCalledTimes(1);
    warn.mockRestore();
  });
});
