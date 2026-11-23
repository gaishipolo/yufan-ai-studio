import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { TemplateCaseUpdateIn } from '@/shared/api/schema';
import { caseApi, type CaseListParams } from './case.api';

/**
 * 案例查询键。
 *
 * 三档口径（`library` / `mine` / `trash`）挂在同一个前缀下，但**列表键里带上
 * 口径与筛选词** —— 否则从「案例库」切到「我的」时命中的是上一档的缓存，列表
 * 不刷新。
 *
 * **键里的每个维度都必须对应一个真实生效的过滤条件**（后端 `case_router.py`
 * 的查询参数），且默认值要与后端一致：`status` 默认空串、`page` 默认 1、
 * `pageSize` 默认 50（后端 `page_size` 的默认值）。键与请求语义错位时，
 * 排查缓存问题会非常困惑——键写着 0、请求拿回来的却是 50 条。
 */
export const caseKeys = {
  all: () => ['template-cases'] as const,
  /* 列表键里带上**分页**：翻页时命中的是上一页的缓存，页码和内容会错位。 */
  list: (params: CaseListParams) =>
    [
      ...caseKeys.all(),
      'list',
      params.scope ?? 'library',
      params.keyword ?? '',
      params.category ?? '',
      // `status` 也是后端支持的过滤条件（别名 status）：不带上它，
      // 不同 status 会命中同一个缓存键 —— 加了筛选却看到上一档数据。
      params.status ?? '',
      params.page ?? 1,
      params.pageSize ?? 50,
    ] as const,
};

export function useTemplateCases(params: CaseListParams = {}) {
  return useQuery({
    queryKey: caseKeys.list(params),
    queryFn: () => caseApi.list(params),
  });
}

/** 案例的写操作：发布 / 编辑 / 下架 / 上架 / 删除 / 恢复。 */
export function useCaseMutations() {
  const client = useQueryClient();
  const refresh = () => {
    // 一个前缀覆盖三档口径 —— 状态一变，案例库与「我的」都要跟着变。
    void client.invalidateQueries({ queryKey: caseKeys.all() });
  };
  return {
    create: useMutation({ mutationFn: caseApi.create, onSettled: refresh }),
    update: useMutation({
      mutationFn: ({ id, payload }: { id: string; payload: TemplateCaseUpdateIn }) =>
        caseApi.update(id, payload),
      onSettled: refresh,
    }),
    offline: useMutation({ mutationFn: caseApi.offline, onSettled: refresh }),
    online: useMutation({ mutationFn: caseApi.online, onSettled: refresh }),
    remove: useMutation({ mutationFn: caseApi.remove, onSettled: refresh }),
    restore: useMutation({ mutationFn: caseApi.restore, onSettled: refresh }),
  };
}
