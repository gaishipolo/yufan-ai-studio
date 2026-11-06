import { AxiosHeaders, type InternalAxiosRequestConfig } from 'axios';
import { describe, expect, it } from 'vitest';
import { mockAdapter } from '@/mocks/adapter';
import type { ApiResponse } from '@/shared/api/types';
import type { PermissionCatalog, Role } from '@/entities/role/model/types';
import { PERMISSION_CODES } from '@/entities/role/model/permission-catalog';
import type { User, UserListResult } from '@/entities/user/model/types';

interface MockResult<T> {
  status: number;
  body: ApiResponse<T>;
}

async function call<T>(
  method: string,
  url: string,
  options: { params?: Record<string, unknown>; data?: unknown } = {},
): Promise<MockResult<T>> {
  const config = {
    url,
    method,
    params: options.params,
    data: options.data === undefined ? undefined : JSON.stringify(options.data),
    headers: new AxiosHeaders(),
  } as unknown as InternalAxiosRequestConfig;

  const response = await mockAdapter(config);
  return { status: response.status, body: response.data as ApiResponse<T> };
}

/** 断言 mock 与真实后端同样「报错」，返回状态码与消息 */
async function expectFailure(
  method: string,
  url: string,
  data?: unknown,
): Promise<{ status: number; message: string }> {
  try {
    await call(method, url, { data });
    throw new Error(`预期失败但成功了：${method} ${url}`);
  } catch (error) {
    const err = error as { response?: { status: number; data: { message: string } }; message: string };
    return {
      status: err.response?.status ?? -1,
      message: err.response?.data?.message ?? err.message,
    };
  }
}

const listUsers = (params: Record<string, unknown> = {}) =>
  call<UserListResult>('get', '/identity/users', { params });

describe('mock adapter · 用户列表', () => {
  it('分页返回 { items, total, page, pageSize }', async () => {
    const { body } = await listUsers({ page: 1, pageSize: 2 });

    expect(body.data?.items).toHaveLength(2);
    expect(body.data?.total).toBe(3);
    expect(body.data?.page).toBe(1);
    expect(body.data?.pageSize).toBe(2);
  });

  it('keyword 匹配用户名 / 昵称 / 邮箱，且大小写不敏感', async () => {
    const byUsername = await listUsers({ keyword: 'admin' });
    const byNickname = await listUsers({ keyword: '小林' });
    const byEmail = await listUsers({ keyword: 'DEMO.LOCAL' });

    expect(byUsername.body.data?.items.map((user) => user.username)).toEqual(['admin']);
    expect(byNickname.body.data?.items.map((user) => user.username)).toEqual(['editor']);
    expect(byEmail.body.data?.total).toBeGreaterThanOrEqual(3);
  });

  it('isActive 与 roleId 筛选可叠加', async () => {
    const inactive = await listUsers({ isActive: 'false' });
    expect(inactive.body.data?.items.every((user) => !user.isActive)).toBe(true);

    const editors = await listUsers({ roleId: 'r_editor' });
    expect(editors.body.data?.items.every((user) => user.roles.some((r) => r.code === 'editor'))).toBe(
      true,
    );
  });

  it('列表项带角色摘要（id / code / name / isSuperuser）', async () => {
    const { body } = await listUsers({ keyword: 'admin' });
    expect(body.data?.items[0].roles[0]).toEqual({
      id: 'r_admin',
      code: 'admin',
      name: '超级管理员',
      isSuperuser: true,
    });
  });
});

describe('mock adapter · 用户写操作', () => {
  it('创建用户返回 201，且能被列表查到', async () => {
    const { status, body } = await call<User>('post', '/identity/users', {
      data: {
        username: 'qa_bot',
        password: 'qa@1234567',
        displayName: '质量机器人',
        roleIds: ['r_viewer'],
      },
    });

    expect(status).toBe(201);
    expect(body.data?.username).toBe('qa_bot');
    expect(body.data?.roles.map((role) => role.code)).toEqual(['viewer']);

    const list = await listUsers({ keyword: 'qa_bot' });
    expect(list.body.data?.total).toBe(1);
  });

  it('重复登录名 409，弱密码 422', async () => {
    const duplicate = await expectFailure('post', '/identity/users', {
      username: 'qa_bot',
      password: 'qa@1234567',
    });
    expect(duplicate.status).toBe(409);

    const weak = await expectFailure('post', '/identity/users', {
      username: 'weak_bot',
      password: '1234567890',
    });
    expect(weak.status).toBe(422);
    expect(weak.message).toContain('纯数字');
  });

  it('分配角色是整组替换', async () => {
    const list = await listUsers({ keyword: 'qa_bot' });
    const user = list.body.data?.items[0] as User;

    const { body } = await call<User>('put', `/identity/users/${user.id}/roles`, {
      data: { roleIds: ['r_editor', 'r_ops'] },
    });

    expect(body.data?.roles.map((role) => role.code)).toEqual(['editor', 'ops']);

    // 传空数组即清空
    const cleared = await call<User>('put', `/identity/users/${user.id}/roles`, {
      data: { roleIds: [] },
    });
    expect(cleared.body.data?.roles).toEqual([]);
  });

  it('分配不存在的角色 404', async () => {
    const list = await listUsers({ keyword: 'qa_bot' });
    const user = list.body.data?.items[0] as User;
    const failed = await expectFailure('put', `/identity/users/${user.id}/roles`, {
      roleIds: ['r_ghost'],
    });
    expect(failed.status).toBe(404);
  });

  it('重置密码读的是 newPassword（字段名回归）', async () => {
    const list = await listUsers({ keyword: 'qa_bot' });
    const user = list.body.data?.items[0] as User;

    const ok = await call<null>('post', `/identity/users/${user.id}/reset-password`, {
      data: { newPassword: 'new@1234567' },
    });
    expect(ok.status).toBe(200);

    // 传错字段名（doc 里曾写成 password）必须被拒，否则前端会在联调时静默失败
    const wrongField = await expectFailure('post', `/identity/users/${user.id}/reset-password`, {
      password: 'new@1234567',
    });
    expect(wrongField.status).toBe(422);
  });

  it('停用最后一个超管被拒绝；停用普通账号返回停用后的用户', async () => {
    const superusers = await listUsers({ keyword: 'admin' });
    const admin = superusers.body.data?.items[0] as User;
    const blocked = await expectFailure('delete', `/identity/users/${admin.id}`);
    expect(blocked.status).toBe(400);
    expect(blocked.message).toContain('超级管理员');

    const list = await listUsers({ keyword: 'qa_bot' });
    const user = list.body.data?.items[0] as User;
    const { body } = await call<User>('delete', `/identity/users/${user.id}`);
    expect(body.data?.isActive).toBe(false);
  });
});

describe('mock adapter · 角色与权限目录', () => {
  it('角色列表是裸数组（不是 { items, total }），并带成员数与失效权限', async () => {
    const { body } = await call<Role[]>('get', '/identity/roles');

    expect(Array.isArray(body.data)).toBe(true);
    const roles = body.data as Role[];
    expect(roles.map((role) => role.code)).toEqual(['admin', 'editor', 'viewer', 'ops']);

    const ops = roles.find((role) => role.code === 'ops') as Role;
    // 种子数据里 viewer 同时挂了 viewer + ops 两个角色
    expect(ops.userCount).toBe(1);
    expect(ops.orphanedPermissions).toEqual(['video:publish']);
  });

  it('内置角色不可删除，有成员的角色也不可删除', async () => {
    const builtin = await expectFailure('delete', '/identity/roles/r_editor');
    expect(builtin.status).toBe(400);

    const created = await call<Role>('post', '/identity/roles', {
      data: { code: 'tmp_role', name: '临时角色', description: '测试用' },
    });
    const role = created.body.data as Role;
    await call('put', `/identity/users/u_002/roles`, { data: { roleIds: [role.id] } });

    const inUse = await expectFailure('delete', `/identity/roles/${role.id}`);
    expect(inUse.status).toBe(400);
    expect(inUse.message).toContain('成员');

    await call('put', `/identity/users/u_002/roles`, { data: { roleIds: ['r_editor'] } });
    const removed = await call<null>('delete', `/identity/roles/${role.id}`);
    expect(removed.status).toBe(200);
  });

  it('提交未登记的权限码被拒（避免保存后“看起来没有却还能访问”）', async () => {
    const failed = await expectFailure('put', '/identity/roles/r_viewer/permissions', {
      permissions: ['task:read', 'video:publish'],
    });
    expect(failed.status).toBe(400);
    expect(failed.message).toContain('video:publish');
  });

  it('权限目录按分组返回，与后端目录同源（21）', async () => {
    const { body } = await call<PermissionCatalog>('get', '/identity/permissions');
    const catalog = body.data as PermissionCatalog;

    // 目录来自后端生成物，经 permission-catalog 薄壳透传
    expect(catalog.total).toBe(PERMISSION_CODES.length);
    expect(catalog.groups.map((group) => group.group)).toEqual(['内容生产', '素材', '组织管理']);
    expect(
      catalog.groups
        .flatMap((group) => group.items)
        .some((item) => item.code === 'identity:role:manage'),
    ).toBe(true);
    // 曾经手抄目录时漏掉的两项：漏了就会在保存角色时被当成未登记码清掉
    for (const code of ['image:read:all', 'task:retry:any']) {
      expect(catalog.groups.flatMap((group) => group.items).some((item) => item.code === code)).toBe(
        true,
      );
    }
  });
});
