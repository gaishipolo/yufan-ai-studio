/*
 * 此文件由 `app export-openapi` 自动生成，请勿手工编辑。
 *
 * 权威来源：后端各模块 `ModuleSpec.permissions`（与 `GET /identity/permissions` 同源）。
 * 前端请从 `shared/api/permissions` 引用，不要再手写一份镜像。
 */

export interface PermissionItem {
  code: string;
  name: string;
  description: string;
}

export interface PermissionGroup {
  group: string;
  items: PermissionItem[];
}

export const PERMISSION_GROUPS: PermissionGroup[] = [
  {
    group: '内容生产',
    items: [
      { code: 'catalog:read', name: '查看模型目录', description: '' },
      { code: 'image:create', name: '提交生图任务', description: '' },
      { code: 'image:read', name: '查看生图历史', description: '' },
      { code: 'image:read:all', name: '查看他人生图历史', description: '列表的数据范围开关；与 task:read:all 同义，不含提交他人的生图任务' },
      { code: 'image:retry:any', name: '重试他人生图任务', description: '只开关「替别人点重试」，不蕴含 image:read:all' },
      { code: 'shot:read', name: '查看镜头库', description: '' },
      { code: 'storyboard:read', name: '查看分镜模板', description: '' },
      { code: 'task:create', name: '创建生成任务', description: '' },
      { code: 'task:delete', name: '删除任务', description: '' },
      { code: 'task:read', name: '查看任务', description: '' },
      { code: 'task:read:all', name: '查看并管理他人任务', description: '列表 scope=all / 详情 / 删除的跨用户判定依据；不含重试' },
      { code: 'task:retry:any', name: '重试他人任务', description: '会消耗额度：重试他人任务前额外校验此权限' },
      { code: 'task:update', name: '编辑/重试自己的任务', description: '' },
      { code: 'template:read', name: '查看行业模板', description: '' },
      { code: 'template:use', name: '使用替换模板并提交零积分生成', description: '' },
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
      { code: 'identity:role:manage', name: '管理角色与权限点', description: '新建/删除角色、调整角色权限、给用户分配角色' },
      { code: 'identity:update', name: '编辑用户（含停用、重置密码）', description: '不含分配角色。改他人昵称/头像：目标不能是超管；重置密码、改他人邮箱、启停账号：目标权限不得高于自己（超管仍需超管）' },
    ],
  },
];

export const PERMISSION_CODES = [
  'asset:create',
  'asset:delete',
  'asset:read',
  'catalog:read',
  'identity:create',
  'identity:delete',
  'identity:read',
  'identity:role:manage',
  'identity:update',
  'image:create',
  'image:read',
  'image:read:all',
  'image:retry:any',
  'shot:read',
  'storyboard:read',
  'task:create',
  'task:delete',
  'task:read',
  'task:read:all',
  'task:retry:any',
  'task:update',
  'template:read',
  'template:use',
] as const;

export type PermissionCode = (typeof PERMISSION_CODES)[number];
