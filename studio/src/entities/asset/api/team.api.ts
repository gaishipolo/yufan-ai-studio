import { useQuery } from '@tanstack/react-query';
import { api } from '@/shared/api/http';

/**
 * 团队（库边界）的客户端。
 *
 * 只读：建团队、加减成员在 M5 的团队管理里。这里提供的只有"我在哪些库里"，
 * 顶栏的库切换器需要它——没有它就只剩个人库与公共库两个入口，
 * 而团队库正是这次改造要立起来的东西。
 */
export interface TeamSummary {
  id: string;
  name: string;
  /** content / ops / tech，前端按它选图标 */
  kind: string;
  /** 我在这个团队里的角色：admin / member；超管看别人团队时为空 */
  role: string;
  memberCount: number;
}

export interface TeamList {
  items: TeamSummary[];
  canManage: boolean;
}

export const teamApi = {
  list: () => api.get<TeamList>('/teams'),
};

export const teamKeys = {all: () => ['teams'] as const};

export function useTeams() {
  return useQuery({
    queryKey: teamKeys.all(),
    queryFn: teamApi.list,
    // 团队关系是低频变化的人事数据：5 分钟内不重查，避免切库时顶栏闪一下
    staleTime: 5 * 60_000,
    retry: 1,
  });
}
