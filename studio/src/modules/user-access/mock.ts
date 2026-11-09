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
  /** 仅展示字段（Q1 未定：不做唯一性与格式校验） */
  phone: string | null;
  position: string | null;
  isActive: boolean;
  lastLoginAt: string | null;
  createdAt: string;
  roleIds: string[];
}

interface MockGroup {
  id: string;
  name: string;
  description: string;
  createdAt: string;
  permissions: string[];
  memberIds: string[];
}

/* 权限点目录来自后端生成物 `shared/api/permissions`（经 permission-catalog 透传），
   由 `app export-openapi` 与后端 `ModuleSpec.permissions` 保持同源。手抄的 19 项曾落后
   后端 2 项（image:read:all / task:retry:any），而 `set_role_permissions` 是整组替换，
   在 mock 里保存一个真实环境带这两项的角色会把它们悄悄清掉。 */

const ALL_CODES = new Set<string>(PERMISSION_CODES);

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

/* ------------------------------------------------------------------
 * 三层授权的 mock 内存数据
 *
 * 这**不是**后端的 seed 演示数据：用户已明确「不要 seed 了，全部都不要了」，
 * 真实库里不会有任何演示小组 / 成员 / 授权。这里只为了让 VITE_USE_MOCK=true
 * 时「分组筛选 + 授权弹窗」能走通（M3 验收项），连真实后端时完全不经过。
 * ------------------------------------------------------------------ */
const groups: MockGroup[] = [
  {
    id: 'g_content',
    name: '内容组',
    description: '模板与素材的编辑同学',
    createdAt: '2026-02-01T00:00:00.000Z',
    permissions: ['asset:create', 'template:read'],
    memberIds: ['u_002'],
  },
  {
    id: 'g_ops',
    name: '运营组',
    description: '代运营，需要看他人的任务',
    createdAt: '2026-02-02T00:00:00.000Z',
    permissions: ['task:read:all'],
    memberIds: ['u_003'],
  },
];

/** 团队层默认 = 内置 viewer 角色的 7 条只读权限（与后端 TEAM_BASE_PERMISSIONS 同源口径） */
let teamPermissions: string[] = [
  'asset:read',
  'catalog:read',
  'image:read',
  'shot:read',
  'storyboard:read',
  'task:read',
  'template:read',
];

/** 个人单独授予；一人可属多组，但个人层只有一份 */
const personalPermissions = new Map<string, string[]>([['u_002', ['asset:update']]]);

const users: MockUser[] = [
  {
    id: 'u_001',
    username: 'admin',
    displayName: '老大',
    email: 'admin@demo.local',
    avatarUrl: null,
    phone: '13800138000',
    position: '超级管理员',
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
    phone: '15576908188',
    position: '编导',
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
    phone: null,
    position: '拍剪',
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
  phone: user.phone,
  position: user.position,
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

/* ------------------------- 三层授权的辅助函数 ------------------------- */

const findGroup = (id: string) => {
  const group = groups.find((item) => item.id === id);
  if (!group) throw new HttpError(404, '小组不存在');
  return group;
};

const toGroupOut = (group: MockGroup) => ({
  id: group.id,
  name: group.name,
  description: group.description,
  createdAt: group.createdAt,
  permissions: [...group.permissions],
  memberIds: [...group.memberIds],
  memberCount: group.memberIds.length,
});

const asCodes = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];

/** 与后端 `_validate_permissions` 对齐：未登记的码一律拒绝，避免静默丢失 */
const validateCodes = (codes: string[]) => {
  for (const code of codes) {
    if (!ALL_CODES.has(code)) throw new HttpError(400, `未登记的权限码：${code}`);
  }
};

/** 团队层面向所有人：`*` 会把全站变超管，`identity:*` 等于给全站发管理权 */
const validateTeamCodes = (codes: string[]) => {
  validateCodes(codes);
  const forbidden = codes.filter((code) => code === '*' || code.startsWith('identity:'));
  if (forbidden.length > 0) {
    throw new HttpError(400, `团队基础权限不允许包含：${forbidden.join('、')}`);
  }
};

/** 四层并集，且来源明细与 `EffectivePermissionsOut` 一一对应 */
const effectiveOf = (user: MockUser) => {
  const groupLayers = groups
    .filter((group) => group.memberIds.includes(user.id))
    .map((group) => ({ id: group.id, name: group.name, permissions: [...group.permissions] }));
  const personal = personalPermissions.get(user.id) ?? [];
  const roleCodes = [
    ...new Set(user.roleIds.flatMap((id) => roleOf(id)?.permissions ?? [])),
  ]
    .filter((code) => ALL_CODES.has(code))
    .sort();
  const codes = new Set<string>([
    ...teamPermissions,
    ...groupLayers.flatMap((layer) => layer.permissions),
    ...personal,
    ...roleCodes,
  ]);
  const isSuperuser =
    user.roleIds.some((id) => roleOf(id)?.isSuperuser) || codes.has('*');
  return {
    team: [...teamPermissions].sort(),
    groups: groupLayers,
    personal: [...personal].sort(),
    roles: roleCodes,
    effective: isSuperuser ? ['*'] : [...codes].sort(),
  };
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
      const name = asText(ctx.params.name).trim().toLowerCase();
      const username = asText(ctx.params.username).trim().toLowerCase();
      const phone = asText(ctx.params.phone).trim().toLowerCase();
      const isActive = ctx.params.isActive;
      const roleId = asText(ctx.params.roleId);
      const groupId = asText(ctx.params.groupId);

      const filtered = users
        .filter((user) => {
          if (keyword) {
            const haystack =
              `${user.username} ${user.displayName} ${user.email ?? ''} ` +
              `${user.phone ?? ''} ${user.position ?? ''}`;
            if (!haystack.toLowerCase().includes(keyword)) return false;
          }
          if (name && !user.displayName.toLowerCase().includes(name)) return false;
          if (username && !user.username.toLowerCase().includes(username)) return false;
          if (phone && !(user.phone ?? '').toLowerCase().includes(phone)) return false;
          if (typeof isActive === 'string' && isActive !== '') {
            if (user.isActive !== (isActive === 'true')) return false;
          }
          if (roleId && !user.roleIds.includes(roleId)) return false;
          if (groupId) {
            const group = groups.find((item) => item.id === groupId);
            if (!group || !group.memberIds.includes(user.id)) return false;
          }
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
      // 与后端 UserCreateIn.groupIds 同口径：去重、不存在报 404、创建后立刻入组
      const groupIds = [...new Set(Array.isArray(ctx.body.groupIds) ? (ctx.body.groupIds as string[]) : [])];
      for (const id of groupIds) findGroup(id);

      const created: MockUser = {
        id: `u_${++seq}`,
        username,
        displayName: asText(ctx.body.displayName) || username,
        email: asText(ctx.body.email) || null,
        avatarUrl: null,
        phone: asText(ctx.body.phone) || null,
        position: asText(ctx.body.position) || null,
        isActive: true,
        lastLoginAt: null,
        createdAt: new Date().toISOString(),
        roleIds,
      };
      users.unshift(created);
      for (const id of groupIds) {
        const group = findGroup(id);
        if (!group.memberIds.includes(created.id)) group.memberIds.push(created.id);
      }
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
      if ('phone' in body) user.phone = asText(body.phone) || null;
      if ('position' in body) user.position = asText(body.position) || null;

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

  /* --------------------- 三层授权：团队 / 小组 / 个人 --------------------- */
  {
    method: 'GET',
    pattern: /^\/identity\/team\/permissions$/,
    handler: () => [...teamPermissions].sort(),
  },
  {
    method: 'PUT',
    pattern: /^\/identity\/team\/permissions$/,
    handler: (ctx) => {
      const codes = [...new Set(asCodes(ctx.body.permissions))];
      validateTeamCodes(codes);
      teamPermissions = codes;
      return [...teamPermissions];
    },
  },
  {
    method: 'GET',
    pattern: /^\/identity\/groups$/,
    // 后端返回裸数组（含 memberIds / permissions / memberCount）
    handler: () =>
      [...groups].sort((a, b) => a.createdAt.localeCompare(b.createdAt)).map(toGroupOut),
  },
  {
    method: 'POST',
    pattern: /^\/identity\/groups$/,
    status: 201,
    handler: (ctx) => {
      const name = asText(ctx.body.name).trim();
      if (!name) throw new HttpError(422, '小组名称不能为空');
      if (groups.some((group) => group.name === name)) {
        throw new HttpError(409, '小组名称已存在');
      }
      const created: MockGroup = {
        id: `g_${++seq}`,
        name,
        description: asText(ctx.body.description),
        createdAt: new Date().toISOString(),
        permissions: [],
        memberIds: [],
      };
      groups.push(created);
      return toGroupOut(created);
    },
  },
  {
    method: 'GET',
    pattern: /^\/identity\/groups\/([\w-]+)$/,
    handler: (ctx) => toGroupOut(findGroup(ctx.path.split('/')[3])),
  },
  {
    method: 'PATCH',
    pattern: /^\/identity\/groups\/([\w-]+)$/,
    handler: (ctx) => {
      const group = findGroup(ctx.path.split('/')[3]);
      const name = asText(ctx.body.name).trim();
      if (name) {
        if (groups.some((item) => item.id !== group.id && item.name === name)) {
          throw new HttpError(409, '小组名称已存在');
        }
        group.name = name;
      }
      if ('description' in ctx.body) group.description = asText(ctx.body.description);
      return toGroupOut(group);
    },
  },
  {
    method: 'DELETE',
    pattern: /^\/identity\/groups\/([\w-]+)$/,
    // 后端删组会同时清成员与该组授权；这里是内存数组，删掉即一并消失
    handler: (ctx) => {
      const group = findGroup(ctx.path.split('/')[3]);
      groups.splice(groups.indexOf(group), 1);
      return null;
    },
  },
  {
    method: 'GET',
    pattern: /^\/identity\/groups\/([\w-]+)\/members$/,
    handler: (ctx) =>
      findGroup(ctx.path.split('/')[3]).memberIds.map((id) => toUserOut(findUser(id))),
  },
  {
    method: 'PUT',
    pattern: /^\/identity\/groups\/([\w-]+)\/members$/,
    handler: (ctx) => {
      const group = findGroup(ctx.path.split('/')[3]);
      const ids = [...new Set(asCodes(ctx.body.userIds))];
      for (const id of ids) findUser(id);
      group.memberIds = ids;
      return toGroupOut(group);
    },
  },
  {
    method: 'PUT',
    pattern: /^\/identity\/groups\/([\w-]+)\/permissions$/,
    handler: (ctx) => {
      const group = findGroup(ctx.path.split('/')[3]);
      const codes = [...new Set(asCodes(ctx.body.permissions))];
      validateCodes(codes);
      group.permissions = codes;
      return toGroupOut(group);
    },
  },
  {
    method: 'GET',
    pattern: /^\/identity\/users\/([\w-]+)\/permissions$/,
    handler: (ctx) => {
      const user = findUser(ctx.path.split('/')[3]);
      return [...(personalPermissions.get(user.id) ?? [])];
    },
  },
  {
    method: 'PUT',
    pattern: /^\/identity\/users\/([\w-]+)\/permissions$/,
    handler: (ctx) => {
      const user = findUser(ctx.path.split('/')[3]);
      const codes = [...new Set(asCodes(ctx.body.permissions))];
      validateCodes(codes);
      personalPermissions.set(user.id, codes);
      return codes;
    },
  },
  {
    method: 'GET',
    pattern: /^\/identity\/users\/([\w-]+)\/effective-permissions$/,
    handler: (ctx) => effectiveOf(findUser(ctx.path.split('/')[3])),
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
