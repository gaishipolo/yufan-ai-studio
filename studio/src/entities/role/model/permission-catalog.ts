/* ------------------------------------------------------------------
 * 权限点目录 —— 前端侧的镜像
 *
 * 权威来源是后端：`permission_catalog()` 汇总各模块 `ModuleSpec.permissions`
 * 后由 `GET /identity/permissions` 下发，角色编辑界面直接渲染它。
 * 前端这份常量**只是**给 mock 模式用的（拿不到后端时也要能勾选角色权限），
 * 所以它必须是"可以自动比对"的镜像，而不是"记得同步"的注释：
 * `permission-catalog.test.ts` 会解析 `api/src/modules/**` 里的 `PermissionSpec`
 * 逐条比对，后端新增/删除权限点而这里没跟上时会直接红。
 *
 * 为什么不用 openapi.json 比对：`PermissionOut.code` 是自由字符串，
 * 目录是运行时数据，schema 里没有枚举，从生成物里抽不出来。
 * ------------------------------------------------------------------ */

export interface PermissionItem {
  code: string;
  name: string;
  description: string;
}

export interface PermissionGroup {
  group: string;
  items: PermissionItem[];
}

/** 与后端 `PermissionSpec` 一一对应；分组名、权限名、描述都照抄后端 */
export const PERMISSION_GROUPS: PermissionGroup[] = [
  {
    group: '内容生产',
    items: [
      { code: 'catalog:read', name: '查看模型目录', description: '' },
      { code: 'image:create', name: '提交生图任务', description: '' },
      { code: 'image:read', name: '查看生图历史', description: '' },
      {
        code: 'image:read:all',
        name: '查看他人生图历史',
        description: '列表的数据范围开关；与 task:read:all 同义，不含提交他人的生图任务',
      },
      { code: 'shot:read', name: '查看镜头库', description: '' },
      { code: 'storyboard:read', name: '查看分镜模板', description: '' },
      { code: 'task:create', name: '创建生成任务', description: '' },
      { code: 'task:delete', name: '删除任务', description: '' },
      { code: 'task:read', name: '查看任务', description: '' },
      {
        code: 'task:read:all',
        name: '查看并管理他人任务',
        description: '列表 scope=all / 详情 / 删除的跨用户判定依据；不含重试',
      },
      {
        code: 'task:retry:any',
        name: '重试他人任务',
        description: '会消耗额度：重试他人任务前额外校验此权限',
      },
      { code: 'task:update', name: '编辑/重试自己的任务', description: '' },
      { code: 'template:read', name: '查看行业模板', description: '' },
    ],
  },
  {
    group: '素材',
    items: [
      { code: 'asset:create', name: '上传素材', description: '' },
      { code: 'asset:delete', name: '删除素材', description: '' },
      { code: 'asset:read', name: '查看素材', description: '' },
    ],
  },
  {
    group: '组织管理',
    items: [
      { code: 'identity:create', name: '创建用户', description: '' },
      { code: 'identity:delete', name: '停用用户', description: '' },
      { code: 'identity:read', name: '查看用户与角色', description: '' },
      {
        code: 'identity:role:manage',
        name: '管理角色与权限点',
        description: '新建/删除角色、调整角色权限、给用户分配角色',
      },
      {
        code: 'identity:update',
        name: '编辑用户（含停用、重置密码）',
        description: '不含分配角色。改他人昵称/头像：目标不能是超管；重置密码、改他人邮箱、启停账号：目标权限不得高于自己（超管仍需超管）',
      },
    ],
  },
];

/** 目录里出现过的全部权限码（角色编辑用「整组替换」，未登记的点会被后端拒绝） */
export const PERMISSION_CODES: string[] = PERMISSION_GROUPS.flatMap((group) =>
  group.items.map((item) => item.code),
).sort();

const KNOWN = new Set(PERMISSION_CODES);

export const isKnownPermission = (code: string): boolean => KNOWN.has(code);

/**
 * 模块声明里的权限点是否在目录内。
 * 通配（`task:*` / `*`）只要求前缀被目录覆盖，否则拼错 `task*` 永远匹配不到任何人。
 */
export function isResolvablePermission(permission: string): boolean {
  if (permission === '*') return true;
  if (permission.endsWith(':*')) {
    const prefix = permission.slice(0, -1);
    return PERMISSION_CODES.some((code) => code.startsWith(prefix));
  }
  return KNOWN.has(permission);
}
