import { HttpError, type MockRoute } from '@/mocks/kernel';
import { USERNAME_PATTERN, checkPassword } from '@/entities/user/model/validation';
import { PERMISSION_CODES, PERMISSION_GROUPS } from '@/entities/role/model/permission-catalog';

/* ------------------------------------------------------------------
 * 用户与权限 · mock
 *
 * 只做一件事：把后端 `modules/identity` 的**真实契约**在内存里复现，
 * 包括分页、201、404、409、以及「权限码未登记要拒绝」这类业务规则。
 * 后端就绪后把 VITE_USE_MOCK 置为 false 即可整体停用，页面与类型都不用改。
 *
 * 刻意没有模拟鉴权（mock 层拿不到会话）：403 由前端路由守卫与后端负责，
 * 想验证 403 请连真实后端并用只读账号登录。
 * ------------------------------------------------------------------ */

interface MockRole {
  id: string;
  code: string;
  name: string;
  description: string;
  isSuperuser: boolean;
  isBuiltin: boolean;
  sortOrder: number;
  createdAt: string;
  permissions: string[];
}

interface MockUser {
  id: string;
  username: string;
  displayName: string;
  email: string | null;
  avatarUrl: string | null;
  isActive: boolean;
  lastLoginAt: string | null;
  createdAt: string;
  roleIds: string[];
}

/* 权限点目录不再手抄：直接引用前端镜像常量（entities/role/model/permission-catalog），
   它由测试与后端 `PermissionSpec` 逐条比对。手抄的 19 项曾落后后端 2 项
   （image:read:all / task:retry:any），而 `set_role_permissions` 是整组替换，
   在 mock 里保存一个真实环境带这两项的角色会把它们悄悄清掉。 */

const ALL_CODES = new Set(PERMISSION_CODES);

const roles: MockRole[] = [
  {
    id: 'r_admin',
    code: 'admin',
    name: '超级管理员',
    description: '拥有全部权限（含未上线模块），不可删除',
    isSuperuser: true,
    isBuiltin: true,
    sortOrder: 0,
    createdAt: '2026-01-01T00:00:00.000Z',
    permissions: [],
  },
  {
    id: 'r_editor',
    code: 'editor',
    name: '内容编辑',
    description: '内容生产全量 + 查看他人任务；不能管理用户与角色',
    isSuperuser: false,
    isBuiltin: true,
    sortOrder: 10,
    createdAt: '2026-01-01T00:00:00.000Z',
    permissions: [
      'asset:create',
      'asset:delete',
      'asset:read',
      'catalog:read',
      'image:create',
      'image:read',
      'shot:read',
      'storyboard:read',
      'task:create',
      'task:delete',
      'task:read',
      'task:read:all',
      'task:update',
      'template:read',
    ],
  },
  {
    id: 'r_viewer',
    code: 'viewer',
    name: '只读成员',
    description: '只读账号：能看自己的任务与全部目录，不能创建与修改',
    isSuperuser: false,
    isBuiltin: true,
    sortOrder: 20,
    createdAt: '2026-01-01T00:00:00.000Z',
    permissions: [
      'asset:read',
      'catalog:read',
      'image:read',
      'shot:read',
      'storyboard:read',
      'task:read',
      'template:read',
    ],
  },
  {
    id: 'r_ops',
    code: 'ops',
    name: '运营',
    description: '可查看与管理他人任务，用于代运营场景',
    isSuperuser: false,
    isBuiltin: false,
    sortOrder: 30,
    createdAt: '2026-02-10T08:00:00.000Z',
    // 故意留一个已下线的权限码，用来验证页面的「失效权限」提示
    permissions: ['task:read', 'task:read:all', 'asset:read', 'video:publish'],
  },
];

const users: MockUser[] = [
  {
    id: 'u_001',
    username: 'admin',
    displayName: '老大',
    email: 'admin@demo.local',
    avatarUrl: null,
    isActive: true,
    lastLoginAt: '2026-11-05T06:18:01.960Z',
    createdAt: '2026-01-01T00:00:00.000Z',
    roleIds: ['r_admin'],
  },
  {
    id: 'u_002',
    username: 'editor',
    displayName: '小林',
    email: 'editor@demo.local',
    avatarUrl: null,
    isActive: true,
    lastLoginAt: null,
    createdAt: '2026-01-02T00:00:00.000Z',
    roleIds: ['r_editor'],
  },
  {
    id: 'u_003',
    username: 'viewer',
    displayName: '阿 May',
    email: 'viewer@demo.local',
    avatarUrl: null,
    isActive: false,
    lastLoginAt: '2026-10-28T09:12:00.000Z',
    createdAt: '2026-01-03T00:00:00.000Z',
    roleIds: ['r_viewer', 'r_ops'],
  },
];

let seq = 100;

const roleOf = (id: string) => roles.find((role) => role.id === id);

const toBrief = (role: MockRole) => ({
  id: role.id,
  code: role.code,
  name: role.name,
  isSuperuser: role.isSuperuser,
});

const toUserOut = (user: MockUser) => ({
  id: user.id,
  username: user.username,
  displayName: user.displayName,
  email: user.email,
  avatarUrl: user.avatarUrl,
  isActive: user.isActive,
  lastLoginAt: user.lastLoginAt,
  createdAt: user.createdAt,
  roles: user.roleIds
    .map((id) => roleOf(id))
    .filter((role): role is MockRole => Boolean(role))
    .map(toBrief),
});

const toRoleOut = (role: MockRole) => ({
  ...role,
  userCount: users.filter((user) => user.roleIds.includes(role.id)).length,
  // 库里留着、目录里已不存在 —— 与后端 `orphanedPermissions` 的口径一致
  orphanedPermissions: role.permissions.filter((code) => !ALL_CODES.has(code)),
});

const findUser = (id: string) => {
  const user = users.find((item) => item.id === id);
  if (!user) throw new HttpError(404, '用户不存在');
  return user;
};

const findRole = (id: string) => {
  const role = roles.find((item) => item.id === id);
  if (!role) throw new HttpError(404, '角色不存在');
  return role;
};

const asNumber = (value: unknown, fallback: number) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : fallback;
};

const asText = (value: unknown) => (typeof value === 'string' ? value : '');

const activeSuperusers = () =>
  users.filter((user) => user.isActive && user.roleIds.some((id) => roleOf(id)?.isSuperuser));

export const routes: MockRoute[] = [
  /* ------------------------------- 用户 ------------------------------- */
  {
    method: 'GET',
    pattern: /^\/identity\/users$/,
    handler: (ctx) => {
      const page = asNumber(ctx.params.page, 1);
      const pageSize = asNumber(ctx.params.pageSize, 10);
      const keyword = asText(ctx.params.keyword).trim().toLowerCase();
      const isActive = ctx.params.isActive;
      const roleId = asText(ctx.params.roleId);

      const filtered = users
        .filter((user) => {
          if (keyword) {
            const haystack = `${user.username} ${user.displayName} ${user.email ?? ''}`.toLowerCase();
            if (!haystack.includes(keyword)) return false;
          }
          if (typeof isActive === 'string' && isActive !== '') {
            if (user.isActive !== (isActive === 'true')) return false;
          }
          if (roleId && !user.roleIds.includes(roleId)) return false;
          return true;
        })
        .sort((a, b) => b.createdAt.localeCompare(a.createdAt));

      const start = (page - 1) * pageSize;
      return {
        items: filtered.slice(start, start + pageSize).map(toUserOut),
        total: filtered.length,
        page,
        pageSize,
      };
    },
  },
  {
    method: 'POST',
    pattern: /^\/identity\/users$/,
    status: 201,
    handler: (ctx) => {
      const username = asText(ctx.body.username).trim().toLowerCase();
      const password = asText(ctx.body.password);
      if (!USERNAME_PATTERN.test(username)) {
        throw new HttpError(422, '用户名 3-64 位，只能是字母、数字或 . _ -');
      }
      const passwordError = checkPassword(password, '密码');
      if (passwordError) throw new HttpError(422, passwordError);
      if (users.some((user) => user.username === username)) {
        throw new HttpError(409, '登录名已存在');
      }

      const roleIds = Array.isArray(ctx.body.roleIds) ? (ctx.body.roleIds as string[]) : [];
      for (const id of roleIds) findRole(id);

      const created: MockUser = {
        id: `u_${++seq}`,
        username,
        displayName: asText(ctx.body.displayName) || username,
        email: asText(ctx.body.email) || null,
        avatarUrl: null,
        isActive: true,
        lastLoginAt: null,
        createdAt: new Date().toISOString(),
        roleIds,
      };
      users.unshift(created);
      return toUserOut(created);
    },
  },
  {
    method: 'GET',
    pattern: /^\/identity\/users\/([\w-]+)$/,
    handler: (ctx) => toUserOut(findUser(ctx.path.split('/')[3])),
  },
  {
    method: 'PATCH',
    pattern: /^\/identity\/users\/([\w-]+)$/,
    handler: (ctx) => {
      const user = findUser(ctx.path.split('/')[3]);
      const body = ctx.body;

      if (typeof body.displayName === 'string') user.displayName = body.displayName;
      if ('email' in body) user.email = asText(body.email) || null;
      if ('avatarUrl' in body) user.avatarUrl = asText(body.avatarUrl) || null;

      if (typeof body.isActive === 'boolean' && body.isActive !== user.isActive) {
        if (!body.isActive) {
          const remaining = activeSuperusers().filter((item) => item.id !== user.id);
          const wasSuperuser = user.roleIds.some((id) => roleOf(id)?.isSuperuser);
          if (wasSuperuser && remaining.length === 0) {
            throw new HttpError(400, '必须保留至少一个启用的超级管理员');
          }
        }
        user.isActive = body.isActive;
      }
      return toUserOut(user);
    },
  },
  {
    method: 'PUT',
    pattern: /^\/identity\/users\/([\w-]+)\/roles$/,
    handler: (ctx) => {
      const user = findUser(ctx.path.split('/')[3]);
      const roleIds = Array.isArray(ctx.body.roleIds) ? (ctx.body.roleIds as string[]) : [];
      for (const id of roleIds) findRole(id);
      user.roleIds = [...new Set(roleIds)];
      return toUserOut(user);
    },
  },
  {
    method: 'POST',
    pattern: /^\/identity\/users\/([\w-]+)\/reset-password$/,
    handler: (ctx) => {
      findUser(ctx.path.split('/')[3]);
      const newPassword = asText(ctx.body.newPassword);
      const passwordError = checkPassword(newPassword, '新密码');
      if (passwordError) throw new HttpError(422, passwordError);
      return null;
    },
  },
  {
    method: 'DELETE',
    pattern: /^\/identity\/users\/([\w-]+)$/,
    handler: (ctx) => {
      const user = findUser(ctx.path.split('/')[3]);
      const wasSuperuser = user.roleIds.some((id) => roleOf(id)?.isSuperuser);
      if (wasSuperuser && activeSuperusers().filter((item) => item.id !== user.id).length === 0) {
        throw new HttpError(400, '必须保留至少一个启用的超级管理员');
      }
      user.isActive = false;
      return toUserOut(user);
    },
  },

  /* ------------------------------- 角色 ------------------------------- */
  {
    method: 'GET',
    pattern: /^\/identity\/roles$/,
    // 后端返回裸数组（不是 { items, total }），这里保持一致
    handler: () =>
      [...roles]
        .sort((a, b) => a.sortOrder - b.sortOrder)
        .map(toRoleOut),
  },
  {
    method: 'POST',
    pattern: /^\/identity\/roles$/,
    status: 201,
    handler: (ctx) => {
      const code = asText(ctx.body.code).trim();
      const name = asText(ctx.body.name).trim();
      if (!/^[a-z][a-z0-9_-]{1,31}$/.test(code)) {
        throw new HttpError(422, '角色标识 2-32 位，小写字母开头');
      }
      if (!name) throw new HttpError(422, '角色名称不能为空');
      if (roles.some((role) => role.code === code)) throw new HttpError(409, '角色标识已存在');

      const permissions = Array.isArray(ctx.body.permissions) ? (ctx.body.permissions as string[]) : [];
      for (const item of permissions) {
        if (!ALL_CODES.has(item)) throw new HttpError(400, `未登记的权限码：${item}`);
      }

      const created: MockRole = {
        id: `r_${++seq}`,
        code,
        name,
        description: asText(ctx.body.description),
        isSuperuser: ctx.body.isSuperuser === true,
        isBuiltin: false,
        sortOrder: (roles[roles.length - 1]?.sortOrder ?? 0) + 10,
        createdAt: new Date().toISOString(),
        permissions,
      };
      roles.push(created);
      return toRoleOut(created);
    },
  },
  {
    method: 'PATCH',
    pattern: /^\/identity\/roles\/([\w-]+)$/,
    handler: (ctx) => {
      const role = findRole(ctx.path.split('/')[3]);
      if (typeof ctx.body.name === 'string' && ctx.body.name.trim()) role.name = ctx.body.name.trim();
      if (typeof ctx.body.description === 'string') role.description = ctx.body.description;
      return toRoleOut(role);
    },
  },
  {
    method: 'PUT',
    pattern: /^\/identity\/roles\/([\w-]+)\/permissions$/,
    handler: (ctx) => {
      const role = findRole(ctx.path.split('/')[3]);
      const permissions = Array.isArray(ctx.body.permissions)
        ? (ctx.body.permissions as string[])
        : [];
      for (const code of permissions) {
        if (!ALL_CODES.has(code)) throw new HttpError(400, `未登记的权限码：${code}`);
      }
      role.permissions = [...new Set(permissions)];
      return toRoleOut(role);
    },
  },
  {
    method: 'DELETE',
    pattern: /^\/identity\/roles\/([\w-]+)$/,
    handler: (ctx) => {
      const role = findRole(ctx.path.split('/')[3]);
      if (role.isBuiltin) throw new HttpError(400, '内置角色不可删除');
      const inUse = users.filter((user) => user.roleIds.includes(role.id)).length;
      if (inUse > 0) throw new HttpError(400, `仍有 ${inUse} 名成员，请先移除`);
      roles.splice(roles.indexOf(role), 1);
      return null;
    },
  },

  /* ----------------------------- 权限目录 ----------------------------- */
  {
    method: 'GET',
    pattern: /^\/identity\/permissions$/,
    handler: () => ({
      groups: PERMISSION_GROUPS,
      total: PERMISSION_GROUPS.reduce((sum, group) => sum + group.items.length, 0),
    }),
  },
];
