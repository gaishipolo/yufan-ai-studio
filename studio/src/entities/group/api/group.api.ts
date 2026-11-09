import { api } from '@/shared/api/http';
import type {
  EffectivePermissions,
  Group,
  GroupCreatePayload,
  GroupMember,
  GroupUpdatePayload,
} from '../model/types';

/**
 * 三层授权接口（后端 `modules/identity/router.py` 的「团队 / 小组 / 个人」段）。
 *
 * 刻意不用 `createRestResource`：这些端点不是同一套 CRUD —— 列表是裸数组、
 * 成员是 `userIds`、授权是 `permissions` 整组替换，还有三个不同 scope 的
 * PUT 共用一份入参形状。套通用工厂只会把差异藏进 `as` 断言。
 *
 * 三个常量本身都是真实端点，而不是一个公共前缀 `/identity`：
 * `shared/api/endpoints.test.ts` 会扫描 api 层里的每个字符串字面量并当成
 * 接口路径去生成物里核对，单独的 `/identity` 后端并不存在，会让契约守卫变红。
 */
const groups = '/identity/groups';
const team = '/identity/team/permissions';
const users = '/identity/users';

export const groupApi = {
  list: () => api.get<Group[]>(groups),
  detail: (id: string) => api.get<Group>(`${groups}/${id}`),
  create: (payload: GroupCreatePayload) => api.post<Group>(groups, payload),
  update: (id: string, payload: GroupUpdatePayload) =>
    api.patch<Group>(`${groups}/${id}`, payload),
  remove: (id: string) => api.delete<null>(`${groups}/${id}`),

  members: (id: string) => api.get<GroupMember[]>(`${groups}/${id}/members`),
  setMembers: (id: string, userIds: string[]) =>
    api.put<Group>(`${groups}/${id}/members`, { userIds }),
  setGroupPermissions: (id: string, permissions: string[]) =>
    api.put<Group>(`${groups}/${id}/permissions`, { permissions }),

  teamPermissions: () => api.get<string[]>(team),
  setTeamPermissions: (permissions: string[]) =>
    api.put<string[]>(team, { permissions }),

  userPermissions: (userId: string) =>
    api.get<string[]>(`${users}/${userId}/permissions`),
  setUserPermissions: (userId: string, permissions: string[]) =>
    api.put<string[]>(`${users}/${userId}/permissions`, { permissions }),
  effectivePermissions: (userId: string) =>
    api.get<EffectivePermissions>(`${users}/${userId}/effective-permissions`),
};
