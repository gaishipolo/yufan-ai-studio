import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/shared/api/http';
import type { SubjectDetail, SubjectList, SubjectPublishResult } from '@/shared/api/schema';

/**
 * 主体的客户端。
 *
 * **可用性不由前端算**：`availability` 是服务端给的 `ready / broken / incomplete`。
 * 前端复算「成员齐不齐、能力够不够」的代价是两份判定规则，而它们一定会漂移——
 * 漂移的表现是卡片写着"可发布"、点下去报"缺少能力"，那是最伤信任的一种不一致。
 *
 * 详情与列表分成两个 hook，不是因为接口分两条，而是**取用节奏不同**：
 * 列表一屏 24 个主体，详情只在点开的那一个才要。合成一个 `useQuery` 会让
 * 打开一页就预取 24 份成员条带。
 */

export const subjectKeys = {
  all: () => ['subjects'] as const,
  list: (params: SubjectQuery) => ['subjects', 'list', params] as const,
  detail: (id: string) => ['subjects', 'detail', id] as const,
};

export interface SubjectQuery {
  subjectType?: string;
  page?: number;
  pageSize?: number;
}

export const subjectApi = {
  list: (params: SubjectQuery = {}) => {
    // 显式摊平成 `Record<string, unknown>`：`api.get` 的 params 是索引签名类型，
    // 直接传 interface 会被 TS 拒（interface 没有隐式索引签名）
    const query: Record<string, unknown> = {
      subjectType: params.subjectType,
      page: params.page,
      pageSize: params.pageSize,
    };
    return api.get<SubjectList>('/subjects', query);
  },
  detail: (id: string) => api.get<SubjectDetail>(`/subjects/${id}`),
  /** B5：旧 `packId` 解析。外部系统与旧书签还在用这个 id */
  byLegacyPack: (packId: string) => api.get<SubjectDetail>(`/subjects/by-legacy-pack/${packId}`),
  publish: (id: string) => api.post<SubjectPublishResult>(`/subjects/${id}/publish`),
};

export function useSubjects(params: SubjectQuery = {}) {
  return useQuery({
    queryKey: subjectKeys.list(params),
    queryFn: () => subjectApi.list(params),
    // 主体变动只发生在用户自己发布/组装时，而那两处都会主动 invalidate
    staleTime: 30_000,
  });
}

export function useSubject(id: string) {
  return useQuery({
    queryKey: subjectKeys.detail(id),
    queryFn: () => subjectApi.detail(id),
    // 空 id 不发请求：浮层关闭时 `id === ''`，`enabled` 关掉才不会打 404
    enabled: Boolean(id),
  });
}

/**
 * 发布。成功后**两处都要失效**：列表里的可用性与版本号变了，详情里的状态也变了。
 * 只失效列表的话，浮层还显示"尚未发布"，用户会再点一次——于是多出一个版本。
 */
export function usePublishSubject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => subjectApi.publish(id),
    onSuccess: (_result, id) => {
      void queryClient.invalidateQueries({queryKey: subjectKeys.all()});
      void queryClient.invalidateQueries({queryKey: subjectKeys.detail(id)});
    },
  });
}
