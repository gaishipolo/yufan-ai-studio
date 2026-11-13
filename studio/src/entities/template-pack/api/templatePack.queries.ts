import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { TemplatePackListOut, TemplatePackOut } from '@/shared/api/schema';
import { templatePackApi } from './templatePack.api';

export const templatePackKeys = {
  all: ['template-packs'] as const,
  detail: (id: string) => ['template-packs', id] as const,
  candidates: (id: string, token: string) => ['template-packs', id, 'candidates', token] as const,
};

/** 已发布的模板库列表（别人也能看的那部分）。 */
export function useTemplatePacks(params?: {ownerId?: string; status?: string}) {
  return useQuery({
    queryKey: [...templatePackKeys.all, params?.ownerId ?? '', params?.status ?? ''],
    queryFn: () => templatePackApi.list(params),
  });
}

/** 单条详情。 */
export function useTemplatePack(id: string | undefined) {
  return useQuery({
    queryKey: templatePackKeys.detail(id ?? ''),
    queryFn: () => templatePackApi.get(id as string),
    enabled: Boolean(id),
  });
}

/**
 * 某个筛选槽位的合规素材池。
 *
 * `token` 变了就换一份池子 —— 池子由服务端的筛选条件算出来，
 * 前端不自己筛（可见性判据在资产库模块里，抄一份必然漂移）。
 */
export function useTemplatePackCandidates(
  id: string | undefined,
  token: string | undefined,
) {
  return useQuery({
    queryKey: templatePackKeys.candidates(id ?? '', token ?? ''),
    queryFn: () => templatePackApi.candidates(id as string, token as string),
    enabled: Boolean(id && token),
  });
}

export function useTemplatePackMutations() {
  const client = useQueryClient();
  const refresh = () => void client.invalidateQueries({queryKey: templatePackKeys.all});
  const refreshOne = (id: string) => {
    void client.invalidateQueries({queryKey: templatePackKeys.detail(id)});
    void client.invalidateQueries({queryKey: templatePackKeys.all});
  };

  const save = useMutation({
    mutationFn: templatePackApi.save,
    onSuccess: saved => refreshOne(saved.id),
  });
  const publish = useMutation({
    mutationFn: templatePackApi.publish,
    onSuccess: published => refreshOne(published.id),
  });
  const disable = useMutation({
    mutationFn: templatePackApi.disable,
    onSuccess: disabled => refreshOne(disabled.id),
  });
  const updatePrompt = useMutation({
    mutationFn: ({id, prompt}: {id: string; prompt: string}) =>
      templatePackApi.updatePrompt(id, {prompt}),
    onSuccess: updated => refreshOne(updated.id),
  });

  return {save, publish, disable, updatePrompt, refresh};
}

/** 列表缓存里就地替换一条，避免整表刷新把滚动位置冲掉。 */
export function patchTemplatePackCache(
  client: ReturnType<typeof useQueryClient>,
  updated: TemplatePackOut,
) {
  client.setQueryData<TemplatePackListOut>(templatePackKeys.all, previous =>
    previous && {
      ...previous,
      items: previous.items?.map(item => (item.id === updated.id ? updated : item)),
    },
  );
}
