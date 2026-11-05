import type { ReactNode } from 'react';
import { useCan } from '@/shared/access/use-access';
import type { Permission } from '@/shared/access/types';
import { ForbiddenState } from '@/shared/ui/ForbiddenState';

/**
 * 路由级守卫。由骨架的 router 工厂自动包裹每个声明了 permissions 的页面，
 * 业务侧不需要手写 <RequirePermission>。
 */
export function RequirePermission({
  permissions,
  children,
}: {
  permissions?: Permission[];
  children: ReactNode;
}) {
  const allowed = useCan(permissions);
  if (allowed) return <>{children}</>;
  return <ForbiddenState permissions={permissions} />;
}
