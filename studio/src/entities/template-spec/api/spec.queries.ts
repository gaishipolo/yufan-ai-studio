import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSessionStore } from '@/shared/access/session';
import type { SpecCompileIn } from '@/shared/api/schema';
import { specApi } from './spec.api';

/** 列表与详情共用一个前缀；发布/停用会改状态，失效范围覆盖同前缀。 */
export const specKeys = {
  all: (owner: string) => ['template-specs', owner] as const,
  list: (owner: string, status: string) => [...specKeys.all(owner), 'list', status] as const,
  detail: (owner: string, key: string) => [...specKeys.all(owner), 'detail', key] as const,
};

export function useSpecs(status = '') {
  const owner = useSessionStore(s => s.subject.id);
  return useQuery({
    queryKey: specKeys.list(owner, status),
    queryFn: () => specApi.list({status: status || undefined, pageSize: 100}),
  });
}

/** 回收站：已软删的规格。与「我的草稿」互斥，所以单开一个查询键。 */
export function useTrashedSpecs() {
  const owner = useSessionStore(s => s.subject.id);
  return useQuery({
    queryKey: [...specKeys.all(owner), 'trash'],
    queryFn: () => specApi.trash({pageSize: 100}),
  });
}

/** 模板库（填表）读的是**已发布**的规格，与「我的草稿」不是一个集合。 */
export function usePublishedSpecs() {
  const owner = useSessionStore(s => s.subject.id);
  return useQuery({
    queryKey: [...specKeys.all(owner), 'published'],
    queryFn: () => specApi.published({pageSize: 100}),
  });
}

/** 编译是纯映射（不落库、不建任务），所以没有失效范围要管。 */
export function useSpecCompile() {
  return useMutation({
    mutationFn: ({key, payload}: {key: string; payload: SpecCompileIn}) =>
      specApi.compile(key, payload),
  });
}

export function useSpecMutations() {
  const client = useQueryClient();
  const owner = useSessionStore(s => s.subject.id);
  const refresh = () => {
    void client.invalidateQueries({queryKey: specKeys.all(owner)});
    // 发布出来的东西会进模板库/任务中心，顺带把这两处刷了。
    void client.invalidateQueries({queryKey: ['template-library', owner]});
  };
  return {
    validate: useMutation({mutationFn: specApi.validate}),
    save: useMutation({mutationFn: specApi.save, onSettled: refresh}),
    publish: useMutation({mutationFn: specApi.publish, onSettled: refresh}),
    disable: useMutation({mutationFn: specApi.disable, onSettled: refresh}),
    remove: useMutation({mutationFn: specApi.remove, onSettled: refresh}),
    restore: useMutation({mutationFn: specApi.restore, onSettled: refresh}),
  };
}
