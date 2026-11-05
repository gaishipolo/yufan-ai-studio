import { describe, expect, it } from 'vitest';
import { can, canAny } from './evaluate';
import type { Subject } from './types';

const subject = (permissions: string[]): Subject => ({
  id: 'u1',
  name: '测试',
  roles: ['member'],
  permissions,
});

describe('权限判定', () => {
  it('未声明要求 = 公开', () => {
    expect(can(subject([]), undefined)).toBe(true);
    expect(can(subject([]), [])).toBe(true);
    expect(can(undefined, [])).toBe(true);
  });

  it('缺权限即拒绝，未登录也拒绝', () => {
    expect(can(subject(['task:read']), 'task:delete')).toBe(false);
    expect(can(undefined, 'task:read')).toBe(false);
  });

  it('资源通配命中同资源的动作', () => {
    expect(can(subject(['task:*']), 'task:delete')).toBe(true);
    expect(can(subject(['task:*']), 'user:delete')).toBe(false);
  });

  it('全量通配命中一切', () => {
    expect(can(subject(['*']), ['user:manage', 'billing:read'])).toBe(true);
  });

  it('数组要求全部满足', () => {
    const s = subject(['task:read']);
    expect(can(s, ['task:read', 'task:delete'])).toBe(false);
    expect(canAny(s, ['task:delete', 'task:read'])).toBe(true);
  });

  it('前缀相同但资源不同不算命中（不能靠 startsWith 蒙混）', () => {
    expect(can(subject(['task:read']), 'task:read:all')).toBe(false);
  });
});
