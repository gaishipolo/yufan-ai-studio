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
      { code: 'export:create', name: '创建导出', description: '' },
      { code: 'export:delete', name: '删除导出', description: '' },
      { code: 'export:read', name: '查看导出', description: '' },
      { code: 'image:create', name: '提交生图任务', description: '' },
      { code: 'image:delete', name: '删除生图记录', description: '删除生成历史，删除后不可再重试；不含删除他人记录（那需要 image:read:all 的数据范围）' },
      { code: 'image:read', name: '查看生图历史', description: '' },
      { code: 'image:read:all', name: '查看他人生图历史', description: '列表的数据范围开关；与 task:read:all 同义，不含提交他人的生图任务' },
      { code: 'image:retry:any', name: '重试他人生图任务', description: '只开关「替别人点重试」，不蕴含 image:read:all' },
      { code: 'model_driver:cancel', name: '取消生成任务', description: '' },
      { code: 'model_driver:create', name: '提交统一生成任务并重试失败记录', description: '' },
      { code: 'model_driver:read', name: '查看模型驱动、模型目录与生成任务', description: '' },
      { code: 'shot:read', name: '查看镜头库', description: '' },
      { code: 'storyboard:read', name: '查看分镜模板', description: '' },
      { code: 'task:create', name: '创建生成任务', description: '' },
      { code: 'task:delete', name: '删除任务', description: '' },
      { code: 'task:read', name: '查看任务', description: '' },
      { code: 'task:read:all', name: '查看并管理他人任务', description: '列表 scope=all / 详情 / 删除的跨用户判定依据；不含重试' },
      { code: 'task:retry:any', name: '重试他人任务', description: '会消耗额度：重试他人任务前额外校验此权限' },
      { code: 'task:update', name: '编辑/重试自己的任务', description: '' },
      { code: 'template_pack:create', name: '创建或保存模板封装草稿', description: '' },
      { code: 'template_pack:publish', name: '发布或停用模板封装', description: '' },
      { code: 'template_pack:read', name: '查看模板封装', description: '' },
    ],
  },
  {
    group: '系统',
    items: [
      { code: 'export:read:audit', name: '导出操作留痕', description: '' },
    ],
  },
  {
    group: '组织管理',
    items: [
      { code: 'identity:create', name: '创建用户', description: '' },
      { code: 'identity:delete', name: '停用用户', description: '' },
      { code: 'identity:group:create', name: '新建小组', description: '' },
      { code: 'identity:group:delete', name: '删除小组', description: '' },
      { code: 'identity:group:member:manage', name: '分配小组成员', description: '整组替换成员；一人可属多组' },
      { code: 'identity:group:permission:manage', name: '设置小组整体权限', description: '该组所有成员自动获得；个人权限只增不减' },
      { code: 'identity:group:read', name: '查看小组与成员', description: '' },
      { code: 'identity:group:update', name: '编辑小组资料', description: '' },
      { code: 'identity:read', name: '查看用户与角色', description: '' },
      { code: 'identity:role:manage', name: '管理角色与权限点', description: '新建/删除角色、调整角色权限、给用户分配角色' },
      { code: 'identity:team:manage', name: '设置团队基础权限', description: '团队 = 所有启用账号；禁止把 identity:* 或 * 放进这一层' },
      { code: 'identity:update', name: '编辑用户（含停用、重置密码）', description: '不含分配角色。改他人昵称/头像：目标不能是超管；重置密码、改他人邮箱、启停账号：目标权限不得高于自己（超管仍需超管）' },
      { code: 'identity:user:permission:manage', name: '设置个人权限', description: '单独授予，与团队/小组/角色取并集；不能授出自己没有的权限' },
    ],
  },
  {
    group: '资产库',
    items: [
      { code: 'library:create', name: '上传与封装资产', description: '' },
      { code: 'library:delete', name: '删除本人资产（移入回收站）', description: '' },
      { code: 'library:delete:any', name: '删除 / 恢复任何人的资产', description: '' },
      { code: 'library:draft:review', name: '审核他人草稿', description: '' },
      { code: 'library:generation:ingest', name: '生成结果入库（草稿箱）', description: '' },
      { code: 'library:read', name: '查看资产库', description: '' },
      { code: 'library:share', name: '发起共享与撤销共享申请', description: '' },
      { code: 'library:share:approve', name: '全站审批共享与撤销申请（兼容码，等同同时勾选下面两项）', description: '' },
      { code: 'library:share:approve:group', name: '审批所在小组的共享与撤销申请', description: '' },
      { code: 'library:share:approve:team', name: '审批团队级共享与撤销申请', description: '' },
      { code: 'library:share:direct', name: '共享无需审批（撤销仍需审批）', description: '' },
      { code: 'library:tag:manage', name: '管理标签词表（改名 / 删除）', description: '' },
      { code: 'library:update', name: '修改本人资产的名称与标签', description: '' },
    ],
  },
];

export const PERMISSION_CODES = [
  'catalog:read',
  'export:create',
  'export:delete',
  'export:read',
  'export:read:audit',
  'identity:create',
  'identity:delete',
  'identity:group:create',
  'identity:group:delete',
  'identity:group:member:manage',
  'identity:group:permission:manage',
  'identity:group:read',
  'identity:group:update',
  'identity:read',
  'identity:role:manage',
  'identity:team:manage',
  'identity:update',
  'identity:user:permission:manage',
  'image:create',
  'image:delete',
  'image:read',
  'image:read:all',
  'image:retry:any',
  'library:create',
  'library:delete',
  'library:delete:any',
  'library:draft:review',
  'library:generation:ingest',
  'library:read',
  'library:share',
  'library:share:approve',
  'library:share:approve:group',
  'library:share:approve:team',
  'library:share:direct',
  'library:tag:manage',
  'library:update',
  'model_driver:cancel',
  'model_driver:create',
  'model_driver:read',
  'shot:read',
  'storyboard:read',
  'task:create',
  'task:delete',
  'task:read',
  'task:read:all',
  'task:retry:any',
  'task:update',
  'template_pack:create',
  'template_pack:publish',
  'template_pack:read',
] as const;

export type PermissionCode = (typeof PERMISSION_CODES)[number];
