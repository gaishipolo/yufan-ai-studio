/* ------------------------------------------------------------------
 * 权限点目录 —— 前端只保留派生逻辑
 *
 * 权威来源是 `shared/api/permissions.ts`，由后端 `app export-openapi` 生成
 * （与各模块 `ModuleSpec.permissions` 汇总的 `GET /identity/permissions` 同源）。
 * 后端 `api/tests/test_openapi_contract.py::test_permission_catalog_is_up_to_date`
 * 做字节比对，改了权限点忘了导出，CI 就红。
 *
 * 这里不再手抄任何一条权限点：以前那份 111 行镜像漂移过两次，而角色权限是
 * 「整组替换」，少一项就会在 mock 模式下把真实环境的权限悄悄清掉。
 * 后端新增/改名权限点只需重新导出生成物，前端无需改动。
 * ------------------------------------------------------------------ */

import {
  PERMISSION_CODES,
  PERMISSION_GROUPS,
  type PermissionCode,
  type PermissionGroup,
  type PermissionItem,
} from '@/shared/api/permissions';

export { PERMISSION_CODES, PERMISSION_GROUPS };
export type { PermissionCode, PermissionGroup, PermissionItem };

// 生成物是 `as const` 的 readonly 字面量元组：不显式给 string，
// `new Set(PERMISSION_CODES)` 会推断成 Set<PermissionCode>，has(string) 直接报错
const KNOWN = new Set<string>(PERMISSION_CODES);

/** 目录里登记过的权限码（角色编辑用「整组替换」，未登记的点会被后端拒绝） */
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
