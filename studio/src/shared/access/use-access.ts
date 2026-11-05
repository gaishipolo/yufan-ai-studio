import { useContext } from 'react';
import { AccessContext } from './access-context';
import { can, canAny } from './evaluate';
import type { Permission } from './types';

/** 当前身份 */
export function useSubject() {
  return useContext(AccessContext);
}

/** UI 级门禁：`const canDelete = useCan('task:delete')` */
export function useCan(permission?: Permission | Permission[] | null): boolean {
  return can(useContext(AccessContext), permission);
}

/** 满足任意一条即可 */
export function useCanAny(permission?: Permission | Permission[] | null): boolean {
  return canAny(useContext(AccessContext), permission);
}
