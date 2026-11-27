import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSessionStore } from '@/shared/access/session';
import type {
  SpecCompileIn,
  modules__template_pack__spec_schema__TagIn,
  modules__template_pack__spec_schema__TagParentIn,
} from '@/shared/api/schema';
import { specApi } from './spec.api';

/** 列表与详情共用一个前缀；发布/停用会改状态，失效范围覆盖同前缀。 */
export const specKeys = {
  all: (owner: string) => ['template-specs', owner] as const,
  list: (owner: string, status: string) => [...specKeys.all(owner), 'list', status] as const,
  detail: (owner: string, key: string) => [...specKeys.all(owner), 'detail', key] as const,
};

/** 标签筛选必须进 queryKey：否则从「产品」切到「角色」会读到上一组缓存。 */
export function useSpecs(status = '', tagIds: string[] = []) {
  const owner = useSessionStore(s => s.subject.id);
  return useQuery({
    queryKey: [...specKeys.list(owner, status), 'tags', tagIds],
    queryFn: () =>
      specApi.list({
        status: status || undefined,
        tagIds: tagIds.length ? tagIds : undefined,
        pageSize: 100,
      }),
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

/**
 * 编辑页要显示的**上限**。
 *
 * 后端常量（`core/template_spec/models.py`），以前前端各抄一份 +
 * 一条「对着 openapi 读一遍」的漂移守卫。现在只有一处真值 —— 调上限只改后端。
 * 上限不会在会话中途变，所以缓存久一点（10 分钟）就够了。
 */
export function useSpecLimits() {
  return useQuery({
    queryKey: ['template-specs', 'limits'],
    queryFn: specApi.limits,
    staleTime: 10 * 60 * 1000,
  });
}

/** 模板库（填表）读的是**已发布**的规格，与「我的草稿」不是一个集合。 */
export function usePublishedSpecs(tagIds: string[] = []) {
  const owner = useSessionStore(s => s.subject.id);
  return useQuery({
    queryKey: [...specKeys.all(owner), 'published', tagIds],
    queryFn: () =>
      specApi.published({
        tagIds: tagIds.length ? tagIds : undefined,
        pageSize: 100,
      }),
    /* 切标签只是换筛选条件，页面结构不变：保留上一份结果，等新数据回来再替换。
       没有它会整页进入 isLoading 骨架屏，标签栏和卡片一起闪掉再重建 ——
       看起来就是整个模板库在抖。 */
    placeholderData: (previous) => previous,
  });
}

/** 模板独立标签词表。词表改动影响所有模板卡片，缓存 30 秒够用。 */
export function useTemplateTags() {
  return useQuery({
    queryKey: ['template-specs', 'tags'],
    queryFn: specApi.tags,
    staleTime: 30_000,
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
    // 一个前缀覆盖全部：`list` / `trash` / `published` 都挂在 `specKeys.all(owner)`
    // 下面，模板库（填表）读的正是 `published` 那一份，所以这里不需要第二句。
    // 曾经还失效过一个 `['template-library', owner]` —— 那个键**不存在**
    // （没有任何查询用它），空转却让人以为有第二个缓存命名空间（审查 3.5）。
    void client.invalidateQueries({queryKey: specKeys.all(owner)});
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

/** 词表管理（改名/移动/删除会改所有模板卡片上的标签显示）。 */
export function useTemplateTagMutations() {
  const client = useQueryClient();
  const refresh = () => {
    void client.invalidateQueries({queryKey: ['template-specs', 'tags']});
    void client.invalidateQueries({queryKey: ['template-specs']});
  };
  return {
    create: useMutation({
      mutationFn: (payload: modules__template_pack__spec_schema__TagIn) => specApi.createTag(payload),
      onSettled: refresh,
    }),
    rename: useMutation({
      mutationFn: ({id, payload}: {id: string; payload: modules__template_pack__spec_schema__TagIn}) =>
        specApi.renameTag(id, payload),
      onSettled: refresh,
    }),
    move: useMutation({
      mutationFn: ({id, payload}: {id: string; payload: modules__template_pack__spec_schema__TagParentIn}) =>
        specApi.moveTag(id, payload),
      onSettled: refresh,
    }),
    remove: useMutation({
      mutationFn: (id: string) => specApi.deleteTag(id),
      onSettled: refresh,
    }),
  };
}

/** 给模板打标签：只改 head 的 tag_ids，不碰草稿/发布。 */
export function useSpecTagMutations() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({key, tagIds}: {key: string; tagIds: string[]}) =>
      specApi.setTags(key, {tagIds}),
    onSettled: () => {
      void client.invalidateQueries({queryKey: ['template-specs']});
    },
  });
}
