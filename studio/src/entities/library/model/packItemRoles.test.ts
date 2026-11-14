import { describe, expect, it } from 'vitest';
import {
  PACK_ITEM_ROLES,
  PACK_ITEM_ROLE_LABELS,
  everyPackRoleKey,
  packItemRoleLabel,
} from './packItemRoles';

/**
 * 这份中性名是「资产库封装没有 kind」的补丁，服务端只把它当自由字符串
 * （`role` 上限 40 字、不校验词表），所以这里守的是「不重不漏」与可读回退：
 * 少一个 key → 下拉里选不到那个用途；出现未登记的 key → 卡片上显示原始英文。
 */
describe('资产库封装成员用途的中性名', () => {
  it('每个 key 都有中性名', () => {
    const missing = everyPackRoleKey().filter((role) => !(role in PACK_ITEM_ROLE_LABELS));
    expect(missing).toEqual([]);
  });

  it('下拉选项与词表同源，不多不少', () => {
    const known = new Set(everyPackRoleKey());
    expect(PACK_ITEM_ROLES.filter((role) => !known.has(role))).toEqual([]);
    expect(PACK_ITEM_ROLES).toHaveLength(known.size);
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
