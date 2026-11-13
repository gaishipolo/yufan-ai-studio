import { describe, expect, it } from 'vitest';
import {
  PACK_ITEM_ROLES,
  PACK_ITEM_ROLE_LABELS,
  everyPackRoleKey,
  packItemRoleLabel,
} from './packItemRoles';

/**
 * 这份中性名是「v2 封装没有 kind」的补丁，不是第二份词表：key 必须与模板词表
 * 完全一致。少一个 → 封装页选不出那个用途（B 端 prepare 会报「没有声明可用途」）；
 * 多一个 → 用户能选一个服务端不认的用途（同样是 400，但错在页面）。
 */
describe('v2 封装成员用途的中性名', () => {
  it('模板词表里出现的每个用途都有中性名', () => {
    const missing = everyPackRoleKey().filter((role) => !(role in PACK_ITEM_ROLE_LABELS));
    expect(missing).toEqual([]);
  });

  it('不出现模板词表里没有的用途', () => {
    const known = new Set(everyPackRoleKey());
    expect(PACK_ITEM_ROLES.filter((role) => !known.has(role))).toEqual([]);
  });

  it('同一个 key 在不同 kind 下是同一个选项（不重复出条目）', () => {
    expect(new Set(PACK_ITEM_ROLES).size).toBe(PACK_ITEM_ROLES.length);
    // front 同时出现在 product / character，但下拉里只有一项
    expect(PACK_ITEM_ROLES.filter((role) => role === 'front')).toHaveLength(1);
  });

  it('未声明用途与未知 key 都有可读回退', () => {
    expect(packItemRoleLabel('')).toBe('未声明用途');
    expect(packItemRoleLabel(undefined)).toBe('未声明用途');
    expect(packItemRoleLabel('multiview')).toBe(PACK_ITEM_ROLE_LABELS.multiview);
    // 原样返回而不是「未声明用途」：历史脏数据要能看见它到底是什么
    expect(packItemRoleLabel('something-else')).toBe('something-else');
  });
});
