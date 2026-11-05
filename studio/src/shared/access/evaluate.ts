import type { Permission, Subject } from './types';

/* ------------------------------------------------------------------
 * 权限判定：纯函数，零依赖，便于单测
 *
 * 支持三种写法（与后端 RBAC 的权限点字符串保持一致）：
 *   精确匹配  task:read
 *   资源通配  task:*      → 命中 task:read / task:delete
 *   全量通配  *           → 命中一切
 * ------------------------------------------------------------------ */

const ALL = '*';

function toList(required?: Permission | Permission[] | null): Permission[] {
  if (required == null) return [];
  return Array.isArray(required) ? required : [required];
}

function grantMatches(grant: Permission, required: Permission): boolean {
  if (grant === ALL || grant === required) return true;
  if (grant.endsWith(':*')) return required.startsWith(grant.slice(0, -1));
  return false;
}

/** 是否满足全部权限要求；未声明要求 = 公开 */
export function can(subject: Subject | null | undefined, required?: Permission | Permission[] | null): boolean {
  const list = toList(required);
  if (list.length === 0) return true;
  if (!subject) return false;
  return list.every((permission) => subject.permissions.some((grant) => grantMatches(grant, permission)));
}

/** 是否满足任意一条（用于「新建或编辑都能看到按钮」这类场景） */
export function canAny(subject: Subject | null | undefined, required?: Permission | Permission[] | null): boolean {
  const list = toList(required);
  if (list.length === 0) return true;
  if (!subject) return false;
  return list.some((permission) => subject.permissions.some((grant) => grantMatches(grant, permission)));
}
