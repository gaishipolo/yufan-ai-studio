import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSessionStore } from '@/shared/access/session';
import { manifestApi } from './manifest.api';

/**
 * 缓存键：列表与详情共用一个前缀。
 *
 * 发布 / 停用会同时改配置状态与（投影出来的）运行表，所以失效范围包含
 * `packs` 与 `replacement` 两组前缀 —— 否则模板库卡片要刷新页面才出现。
 */
export const manifestKeys = {
  all: (owner: string) => ['template-manifests', owner] as const,
  list: (owner: string) => [...manifestKeys.all(owner), 'list'] as const,
  detail: (owner: string, key: string) => [...manifestKeys.all(owner), 'detail', key] as const,
};

export function useManifests() {
  const owner = useSessionStore(s => s.subject.id);
  return useQuery({
    queryKey: manifestKeys.list(owner),
    queryFn: () => manifestApi.list(),
  });
}

export function useManifestMutations() {
  const client = useQueryClient();
  const owner = useSessionStore(s => s.subject.id);
  const refresh = () => {
    void client.invalidateQueries({queryKey: manifestKeys.all(owner)});
    void client.invalidateQueries({queryKey: ['packs', owner]});
    void client.invalidateQueries({queryKey: ['replacement', owner]});
    void client.invalidateQueries({queryKey: ['video-templates', owner]});
  };
  return {
    validate: useMutation({mutationFn: manifestApi.validate}),
    save: useMutation({mutationFn: manifestApi.save, onSettled: refresh}),
    publish: useMutation({mutationFn: manifestApi.publish, onSettled: refresh}),
    disable: useMutation({mutationFn: manifestApi.disable, onSettled: refresh}),
  };
}
