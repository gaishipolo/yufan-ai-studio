import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSessionStore } from '@/shared/access/session';
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
  };
}
