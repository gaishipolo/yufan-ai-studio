import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createQueryKeys } from '@/shared/api/rest';
import { roleApi } from './role.api';
import type { RoleCreatePayload, RoleUpdatePayload } from '../model/types';

/** 查询键工厂：`roles` 命名空间 */
export const roleKeys = createQueryKeys('roles');

export function useRoleList() {
  return useQuery({
    queryKey: roleKeys.lists(),
    queryFn: () => roleApi.list(),
    staleTime: 30_000,
  });
}

/** 权限点目录：随代码发布变化，一次请求后长期可用 */
export function usePermissionCatalog() {
  return useQuery({
    queryKey: [...roleKeys.all, 'permissions'] as const,
    queryFn: () => roleApi.permissionCatalog(),
    staleTime: Infinity,
  });
}

export function useCreateRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: RoleCreatePayload) => roleApi.create(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: roleKeys.all });
    },
  });
}

export function useUpdateRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: RoleUpdatePayload }) =>
      roleApi.update(id, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: roleKeys.all });
    },
  });
}

export function useSetRolePermissions() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, permissions }: { id: string; permissions: string[] }) =>
      roleApi.setPermissions(id, permissions),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: roleKeys.all });
    },
  });
}

export function useDeleteRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => roleApi.remove(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: roleKeys.all });
    },
  });
}
