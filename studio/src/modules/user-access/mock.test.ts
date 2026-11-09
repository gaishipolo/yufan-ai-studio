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

  it('权限目录按分组返回，且分组与码能互相覆盖', async () => {
    const { body } = await call<PermissionCatalog>('get', '/identity/permissions');
    const catalog = body.data as PermissionCatalog;

    // 目录来自后端生成物，经 permission-catalog 薄壳透传
    expect(catalog.total).toBe(PERMISSION_CODES.length);

    // 这里原本硬编码着 `['内容生产', '素材', '组织管理']`。导出功能加了「系统」
    // 一组之后它就红了，而红着一直没人判断「该不该把这一组放进前端目录」——
    // 抄下来的清单，改与不改都说不清理由，只能靠人记得。改成检查目录自身的自洽：
    // 组不重名、码不重复、每个权限码都被某一组收着且只收一次。
    // 这三条与「后端新加了几组」无关，但能抓住真正的坏目录（漏分组、重名、码丢失）。
    const names = catalog.groups.map((group) => group.group);
    expect(new Set(names).size).toBe(names.length);

    const codes = catalog.groups.flatMap((group) => group.items);
    expect(new Set(codes.map((item) => item.code)).size).toBe(codes.length);
    expect(codes.map((item) => item.code).sort()).toEqual([...PERMISSION_CODES].sort());
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

describe('mock adapter · 团队 / 小组 / 个人三层授权', () => {
  it('用户列表支持名字 / 账号 / 手机号 / 小组四路筛选', async () => {
    const byName = await listUsers({ name: '小林' });
    expect(byName.body.data?.items.map((user) => user.username)).toEqual(['editor']);

    const byUsername = await listUsers({ username: 'VIEW' });
    expect(byUsername.body.data?.items.map((user) => user.username)).toEqual(['viewer']);

    const byPhone = await listUsers({ phone: '1557690' });
    expect(byPhone.body.data?.items.map((user) => user.username)).toEqual(['editor']);

    const byGroup = await listUsers({ groupId: 'g_content' });
    expect(byGroup.body.data?.items.map((user) => user.username)).toEqual(['editor']);
  });

  it('用户出参带 phone / position，创建与编辑都能写这两个展示字段', async () => {
    const editor = (await listUsers({ username: 'editor' })).body.data?.items[0] as User;
    expect(editor.phone).toBe('15576908188');
    expect(editor.position).toBe('编导');

    const created = await call<User>('post', '/identity/users', {
      data: { username: 'contact_bot', password: 'contact@1234', phone: '13600000000', position: '剪辑' },
    });
    expect(created.body.data?.phone).toBe('13600000000');
    expect(created.body.data?.position).toBe('剪辑');

    const patched = await call<User>('patch', `/identity/users/${created.body.data?.id}`, {
      data: { phone: null, position: '统筹' },
    });
    expect(patched.body.data?.phone).toBeNull();
    expect(patched.body.data?.position).toBe('统筹');
  });

  it('小组列表是裸数组并带成员与权限；重名 409，删除后详情 404', async () => {
    const { body } = await call<{ id: string; name: string; memberIds: string[] }[]>('get', '/identity/groups');
    const groups = body.data as { id: string; name: string; memberIds: string[] }[];
    expect(Array.isArray(groups)).toBe(true);
    expect(groups.find((group) => group.id === 'g_content')?.memberIds).toEqual(['u_002']);

    const duplicate = await expectFailure('post', '/identity/groups', { name: '内容组' });
    expect(duplicate.status).toBe(409);

    const created = await call<{ id: string; name: string }>('post', '/identity/groups', {
      data: { name: '临时小组', description: '测试用' },
    });
    expect(created.status).toBe(201);
    const id = (created.body.data as { id: string }).id;

    await call('delete', `/identity/groups/${id}`);
    const missing = await expectFailure('get', `/identity/groups/${id}`);
    expect(missing.status).toBe(404);
  });

  it('成员是整组替换，替换后 groupId 筛选与有效权限同时生效', async () => {
    const before = await call<{ id: string }[]>('get', '/identity/groups/g_content/members');
    expect((before.body.data as { id: string }[]).map((user) => user.id)).toEqual(['u_002']);

    await call('put', '/identity/groups/g_content/members', {
      data: { userIds: ['u_002', 'u_003'] },
    });
    const byGroup = await listUsers({ groupId: 'g_content' });
    expect(byGroup.body.data?.items.map((user) => user.id).sort()).toEqual(['u_002', 'u_003']);

    // 复原，别让后面的用例依赖这一条的顺序
    await call('put', '/identity/groups/g_content/members', { data: { userIds: ['u_002'] } });
  });

  it('有效权限按四层摊开，判定字段是 effective；超管短路为 *', async () => {
    const { body } = await call<{
      team: string[];
      groups: { id: string; name: string; permissions: string[] }[];
      personal: string[];
      roles: string[];
      effective: string[];
    }>('get', '/identity/users/u_002/effective-permissions');
    const detail = body.data as {
      team: string[];
      groups: { id: string; name: string; permissions: string[] }[];
      personal: string[];
      roles: string[];
      effective: string[];
    };

    expect(detail.team).toContain('task:read');
    expect(detail.groups.map((group) => group.name)).toContain('内容组');
    expect(detail.personal).toContain('asset:update');
    expect(detail.roles).toContain('template:read');
    // 并集：个人层的码必须出现在 effective 里，而不是只躺在 personal
    expect(detail.effective).toContain('asset:update');
    expect(detail.effective).toContain('task:read');

    const admin = await call<{ effective: string[] }>(
      'get',
      '/identity/users/u_001/effective-permissions',
    );
    expect(admin.body.data?.effective).toEqual(['*']);
  });

  it('团队权限整组替换；未登记码、管理类与通配一律拒绝', async () => {
    const before = (await call<string[]>('get', '/identity/team/permissions')).body.data as string[];
    try {
      const forbidden = await expectFailure('put', '/identity/team/permissions', {
        permissions: ['task:read', 'identity:group:read'],
      });
      expect(forbidden.status).toBe(400);
      expect(forbidden.message).toContain('identity:group:read');

      const wildcard = await expectFailure('put', '/identity/team/permissions', {
        permissions: ['*'],
      });
      expect(wildcard.status).toBe(400);

      const unknown = await expectFailure('put', '/identity/team/permissions', {
        permissions: ['video:publish'],
      });
      expect(unknown.status).toBe(400);
      expect(unknown.message).toContain('未登记');

      const updated = await call<string[]>('put', '/identity/team/permissions', {
        data: { permissions: ['task:read'] },
      });
      expect(updated.body.data).toEqual(['task:read']);
    } finally {
      await call('put', '/identity/team/permissions', { data: { permissions: before } });
    }
  });

  it('小组与个人层的授权都是整组替换，且拒绝未登记的码', async () => {
    const group = await call<{ permissions: string[] }>('put', '/identity/groups/g_content/permissions', {
      data: { permissions: ['asset:create', 'asset:update'] },
    });
    expect(group.body.data?.permissions).toEqual(['asset:create', 'asset:update']);

    const personal = await call<string[]>('put', '/identity/users/u_002/permissions', {
      data: { permissions: ['asset:share'] },
    });
    expect(personal.body.data).toEqual(['asset:share']);
    const reread = await call<string[]>('get', '/identity/users/u_002/permissions');
    expect(reread.body.data).toEqual(['asset:share']);

    const unknown = await expectFailure('put', '/identity/users/u_002/permissions', {
      permissions: ['video:publish'],
    });
    expect(unknown.status).toBe(400);

    // 复原
    await call('put', '/identity/groups/g_content/permissions', {
      data: { permissions: ['asset:create', 'template:read'] },
    });
    await call('put', '/identity/users/u_002/permissions', { data: { permissions: ['asset:update'] } });
  });

  it('建号带 groupIds 会立刻入组并继承组权限；未知小组 404', async () => {
    const created = await call<User>('post', '/identity/users', {
      data: {
        username: 'joiner_bot',
        password: 'join@123456',
        // 重复 id 只应插入一条成员
        groupIds: ['g_content', 'g_content'],
      },
    });
    expect(created.status).toBe(201);
    const userId = (created.body.data as User).id;

    const members = await call<User[]>('get', '/identity/groups/g_content/members');
    expect((members.body.data as User[]).filter((user) => user.id === userId)).toHaveLength(1);

    const detail = await call<{
      groups: { id: string }[];
      effective: string[];
    }>('get', `/identity/users/${userId}/effective-permissions`);
    expect(detail.body.data?.groups.map((group) => group.id)).toContain('g_content');
    expect(detail.body.data?.effective).toContain('asset:create');

    const missing = await expectFailure('post', '/identity/users', {
      username: 'ghost_joiner',
      password: 'join@123456',
      groupIds: ['g_ghost'],
    });
    expect(missing.status).toBe(404);

    // 复原：别让后面的用例依赖这个新成员
    await call('put', '/identity/groups/g_content/members', { data: { userIds: ['u_002'] } });
  });
});
