import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
// 三层授权变化会改变「某个账号的有效权限」，用户列表与角色卡片上的缓存都要重取。
// 依赖方向单向 group → user → role，不成环。
import { userKeys } from '@/entities/user/api/user.queries';
import { userApi } from '@/entities/user/api/user.api';
import { groupApi } from './group.api';
import type { GroupCreatePayload, GroupUpdatePayload } from '../model/types';

/**
 * 三层授权的查询键。
 *
 * 没有用 `createQueryKeys`：这里除了 list 还有 team / members / 个人权限 /
 * 有效权限四种键，工厂的 `list(params)` / `detail(id)` 形状一个都用不上，
 * 套进去只会把「这个键到底是哪一层」藏进参数里。
 */
export const groupKeys = {
  all: ['groups'] as const,
  list: () => ['groups', 'list'] as const,
  members: (id: string) => ['groups', 'members', id] as const,
  team: () => ['groups', 'team'] as const,
  userPermissions: (id: string) => ['groups', 'user-permissions', id] as const,
  effective: (id: string) => ['groups', 'effective', id] as const,
};

/** 任一层授权变了，这份数据的所有读缓存都不再可信 */
function useInvalidateLayers() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: groupKeys.all });
    void qc.invalidateQueries({ queryKey: userKeys.all });
  };
}

export function useGroupList() {
  return useQuery({
    queryKey: groupKeys.list(),
    queryFn: () => groupApi.list(),
    staleTime: 10_000,
  });
}

export function useGroupMembers(id: string | null) {
  return useQuery({
    queryKey: groupKeys.members(id ?? ''),
    queryFn: () => groupApi.members(id as string),
    enabled: Boolean(id),
  });
}

/**
 * 小组「成员」页签的候选账号，取第一页上限 100 条（后端 MAX_PAGE_SIZE = 100）。
 * 放在查询层而不是 feature 里裸调 `useQuery`：编辑弹窗关闭时这张表不该发请求，
 * 而且页面测试只需替掉一个模块就能覆盖全部取数。
 */
export function useMemberCandidates(enabled: boolean) {
  return useQuery({
    queryKey: userKeys.list({ page: 1, pageSize: 100 }),
    queryFn: () => userApi.list({ page: 1, pageSize: 100 }),
    enabled,
  });
}

export function useTeamPermissions() {
  return useQuery({
    queryKey: groupKeys.team(),
    queryFn: () => groupApi.teamPermissions(),
  });
}

export function useUserPermissions(userId: string | null) {
  return useQuery({
    queryKey: groupKeys.userPermissions(userId ?? ''),
    queryFn: () => groupApi.userPermissions(userId as string),
    enabled: Boolean(userId),
  });
}

export function useEffectivePermissions(userId: string | null) {
  return useQuery({
    queryKey: groupKeys.effective(userId ?? ''),
    queryFn: () => groupApi.effectivePermissions(userId as string),
    enabled: Boolean(userId),
  });
}

export function useCreateGroup() {
  const invalidate = useInvalidateLayers();
  return useMutation({
    mutationFn: (payload: GroupCreatePayload) => groupApi.create(payload),
    onSuccess: invalidate,
  });
}

export function useUpdateGroup() {
  const invalidate = useInvalidateLayers();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: GroupUpdatePayload }) =>
      groupApi.update(id, payload),
    onSuccess: invalidate,
  });
}

export function useDeleteGroup() {
  const invalidate = useInvalidateLayers();
  return useMutation({
    mutationFn: (id: string) => groupApi.remove(id),
    onSuccess: invalidate,
  });
}

export function useSetGroupMembers() {
  const invalidate = useInvalidateLayers();
  return useMutation({
    mutationFn: ({ id, userIds }: { id: string; userIds: string[] }) =>
      groupApi.setMembers(id, userIds),
    onSuccess: invalidate,
  });
}

export function useSetGroupPermissions() {
  const invalidate = useInvalidateLayers();
  return useMutation({
    mutationFn: ({ id, permissions }: { id: string; permissions: string[] }) =>
      groupApi.setGroupPermissions(id, permissions),
    onSuccess: invalidate,
  });
}

export function useSetTeamPermissions() {
  const invalidate = useInvalidateLayers();
  return useMutation({
    mutationFn: (permissions: string[]) => groupApi.setTeamPermissions(permissions),
    onSuccess: invalidate,
  });
}

export function useSetUserPermissions() {
  const invalidate = useInvalidateLayers();
  return useMutation({
    mutationFn: ({ id, permissions }: { id: string; permissions: string[] }) =>
      groupApi.setUserPermissions(id, permissions),
    onSuccess: invalidate,
  });
}
