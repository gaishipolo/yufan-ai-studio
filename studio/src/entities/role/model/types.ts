/**
 * 角色与权限点类型 —— 对应后端 `RoleOut` / `PermissionCatalogOut`。
 *
 * 两个容易踩的点，字段名已按后端实际返回校准：
 * - `GET /identity/roles` 返回的是**裸数组**，不是 `{ items, total }`
 * - `permissions` 里可能混入已下线的权限码（见 `orphanedPermissions`），
 *   页面必须把这种情况提示出来，否则会出现「明明取消了却还能访问」的错觉
 */

export interface Role {
  id: string;
  /** 唯一标识，内置角色不可改 */
  code: string;
  name: string;
  description: string;
  /** 持有即展开为 `*` */
  isSuperuser: boolean;
  /** 内置角色：不可删除 */
  isBuiltin: boolean;
  sortOrder: number;
  createdAt: string;
  permissions: string[];
  /** 使用该角色的用户数 */
  userCount: number;
  /** 库里还留着、但权限点已下线（模块删除 / 权限码改名） */
  orphanedPermissions: string[];
}

export interface RoleCreatePayload {
  code: string;
  name: string;
  description?: string;
  permissions?: string[];
  isSuperuser?: boolean;
}

export interface RoleUpdatePayload {
  name?: string;
  description?: string;
  isSuperuser?: boolean;
}

export interface PermissionItem {
  code: string;
  name: string;
  description: string;
}

export interface PermissionGroup {
  group: string;
  items: PermissionItem[];
}

export interface PermissionCatalog {
  groups: PermissionGroup[];
  total: number;
}

/** 角色是否可删：内置角色不行；仍有成员时后端也会拒绝 */
export function cannotDeleteReason(role: Role): string | null {
  if (role.isBuiltin) return '内置角色不可删除';
  if (role.userCount > 0) return `仍有 ${role.userCount} 名成员，请先移除`;
  return null;
}

/** 超管角色的权限是隐式的 `*`，界面上不要显示成「0 个权限」 */
export function permissionCountLabel(role: Role): string {
  return role.isSuperuser ? '全部权限（*）' : `${role.permissions.length} 个权限点`;
}
