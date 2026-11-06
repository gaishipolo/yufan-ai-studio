import { describe, expect, it } from 'vitest';
import * as generated from '@/shared/api/permissions';
import {
  PERMISSION_CODES,
  PERMISSION_GROUPS,
  isKnownPermission,
  isResolvablePermission,
} from './permission-catalog';

/* ------------------------------------------------------------------
 * 目录内容由后端生成物 + 字节比对守卫保证，前端不再解析 `api/src/modules/**`。
 * 旧做法有两个毛病：单独检出 studio 时那条比对齐会静默跳过；而且它只是
 * 「比对」，红了还得人去改。这里只测前端自己的用法，外加守住「薄壳没有
 * 偷偷再手抄一份」——后者一旦被绕过，漂移就又回来了。
 * ------------------------------------------------------------------ */

describe('权限点目录', () => {
  it('直接透传生成物，本层没有手抄副本', () => {
    expect(PERMISSION_CODES).toBe(generated.PERMISSION_CODES);
    expect(PERMISSION_GROUPS).toBe(generated.PERMISSION_GROUPS);
  });

  it('没有重复的权限码', () => {
    expect(new Set(PERMISSION_CODES).size).toBe(PERMISSION_CODES.length);
  });

  it('码表与分组内容一致', () => {
    const fromGroups = PERMISSION_GROUPS.flatMap((group) =>
      group.items.map((item) => item.code),
    ).sort();
    expect([...PERMISSION_CODES].sort()).toEqual(fromGroups);
  });

  it('每个分组都有内容', () => {
    for (const group of PERMISSION_GROUPS) {
      expect(group.items.length, `分组「${group.group}」是空的`).toBeGreaterThan(0);
    }
  });

  it('通配写法能解析到具体权限点，拼错的前缀解析不到', () => {
    expect(isResolvablePermission('*')).toBe(true);
    expect(isResolvablePermission('task:*')).toBe(true);
    expect(isResolvablePermission('task:read')).toBe(true);
    expect(isResolvablePermission('task*')).toBe(false);
    expect(isResolvablePermission('task:reed')).toBe(false);
  });

  it('isKnownPermission 与码表一致', () => {
    expect(isKnownPermission('task:read')).toBe(true);
    expect(isKnownPermission('task:reed')).toBe(false);
  });
});
