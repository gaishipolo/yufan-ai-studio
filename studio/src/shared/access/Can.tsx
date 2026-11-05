import type { ReactNode } from 'react';
import { useCan } from './use-access';
import type { Permission } from './types';

export interface CanProps {
  /** 需要的权限，数组表示「全部满足」 */
  permission: Permission | Permission[];
  /** 无权限时的替代内容，默认不渲染 */
  fallback?: ReactNode;
  children: ReactNode;
}

/**
 * UI 级门禁。只负责「藏起来」，不负责安全 ——
 * 真正的拦截在路由守卫与后端，二者必须同时存在。
 *
 * @example
 * <Can permission="task:delete">
 *   <Button variant="danger" onClick={onDelete}>删除</Button>
 * </Can>
 */
export function Can({ permission, fallback = null, children }: CanProps) {
  return <>{useCan(permission) ? children : fallback}</>;
}
