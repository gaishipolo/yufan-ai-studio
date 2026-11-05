import { api } from '@/shared/api/http';
import type {
  PermissionCatalog,
  Role,
  RoleCreatePayload,
  RoleUpdatePayload,
} from '../model/types';

/**
 * 角色与权限点接口。
 *
 * 刻意不用 `createRestResource`：角色列表不分页（返回裸数组）、也没有 `GET /roles/{id}`，
 * 硬套工厂会得到一个「类型看着对、请求根本不存在」的 `detail()`。
 */
const base = '/identity/roles';

export const roleApi = {
  list: () => api.get<Role[]>(base),

  create: (payload: RoleCreatePayload) => api.post<Role>(base, payload),

  update: (id: string, payload: RoleUpdatePayload) => api.patch<Role>(`${base}/${id}`, payload),

  /** 整组替换角色权限；出现未登记的权限码后端会拒绝 */
  setPermissions: (id: string, permissions: string[]) =>
    api.put<Role>(`${base}/${id}/permissions`, { permissions }),

  /** 内置角色 / 仍有成员时后端会拒绝，错误信息直接透传到页面 */
  remove: (id: string) => api.delete<null>(`${base}/${id}`),

  /** 全部权限点（按分组），角色编辑界面直接渲染 */
  permissionCatalog: () => api.get<PermissionCatalog>('/identity/permissions'),
};
