import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createQueryKeys } from '@/shared/api/rest';
// 角色卡片上展示「成员数」，用户变更会影响它 → 复用角色实体的键，避免写裸字符串。
// 依赖方向是单向的 user → role，role 不反向依赖 user，不会成环。
import { roleKeys } from '@/entities/role/api/role.queries';
import { userApi } from './user.api';
import type { CreateUserPayload, UpdateUserPayload, UserListParams } from '../model/types';

/** 查询键工厂：`users` 命名空间，按资源整体失效不会误伤其它实体 */
export const userKeys = createQueryKeys<UserListParams>('users');

const PAGE_SIZE = 10;

export function useUserList(params: UserListParams) {
  return useQuery({
    queryKey: userKeys.list({ pageSize: PAGE_SIZE, ...params }),
    queryFn: () => userApi.list({ pageSize: PAGE_SIZE, ...params }),
    // 翻页 / 改筛选时保留上一页数据，避免整块闪白
    placeholderData: keepPreviousData,
    staleTime: 10_000,
  });
}

export function useCreateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateUserPayload) => userApi.create(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: userKeys.all });
      // 角色卡片上的成员数也变了
      void qc.invalidateQueries({ queryKey: roleKeys.all });
    },
  });
}

export function useUpdateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: UpdateUserPayload }) =>
      userApi.update(id, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: userKeys.all });
    },
  });
}

export function useSetUserRoles() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, roleIds }: { id: string; roleIds: string[] }) =>
      userApi.setRoles(id, roleIds),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: userKeys.all });
      void qc.invalidateQueries({ queryKey: roleKeys.all });
    },
  });
}

export function useResetPassword() {
  return useMutation({
    mutationFn: ({ id, newPassword }: { id: string; newPassword: string }) =>
      userApi.resetPassword(id, newPassword),
  });
}

export function useDeactivateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => userApi.deactivate(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: userKeys.all });
    },
  });
}
