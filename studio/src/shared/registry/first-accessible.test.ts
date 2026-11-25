import { Boxes } from 'lucide-react';
import { describe, expect, it } from 'vitest';
import { HOME_PATH } from '@/shared/config/app';
import type { Subject } from '@/shared/access/types';
import { createRegistry } from './create-registry';
import { firstAccessiblePath } from './first-accessible';
import { defineModule } from './define-module';

const load = () => Promise.resolve({ default: () => null });

const registry = createRegistry([
  defineModule({
    id: 'task-center',
    routes: [
      {
        path: 'tasks',
        load,
        permissions: ['page:task-center'],
        nav: { label: '任务中心', icon: Boxes, group: '资产与数据', groupOrder: 3, order: 1 },
      },
    ],
  }),
  defineModule({
    id: 'studio',
    routes: [
      {
        path: 'studio',
        load,
        permissions: ['page:studio'],
        nav: { label: '生成工作台', icon: Boxes, group: '创作', groupOrder: 1, order: 1 },
      },
    ],
  }),
  defineModule({
    id: 'image',
    routes: [
      {
        path: 'image',
        load,
        permissions: ['page:image'],
        nav: { label: '生图', icon: Boxes, group: '创作', groupOrder: 1, order: 2 },
      },
    ],
  }),
]);

const subject = (permissions: string[]): Subject => ({
  id: 'u_test',
  name: '测试',
  roles: [],
  permissions,
});

describe('firstAccessiblePath · 登录落地页', () => {
  it('按侧栏顺序取第一个有权限的页面，而不是写死的首页', () => {
    expect(firstAccessiblePath(subject(['page:image']), registry)).toBe('/image');
  });

  it('有两页权限时取排序靠前的那页（创作组在资产组之前）', () => {
    expect(
      firstAccessiblePath(subject(['page:task-center', 'page:image']), registry),
    ).toBe('/image');
  });

  it('超管 * 落到侧栏第一项', () => {
    expect(firstAccessiblePath(subject(['*']), registry)).toBe('/studio');
  });

  it('权限码前缀匹配也算可访问（page:*）', () => {
    expect(firstAccessiblePath(subject(['page:*']), registry)).toBe('/studio');
  });

  it('一个页面权限都没有时回退固定首页', () => {
    expect(firstAccessiblePath(subject([]), registry)).toBe(HOME_PATH);
    expect(firstAccessiblePath(null, registry)).toBe(HOME_PATH);
  });
});
