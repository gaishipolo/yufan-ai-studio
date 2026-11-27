import { useMemo } from 'react';
import type { LibraryTagStatOut, TagOut } from '@/shared/api/schema';
import { descendantIds } from '@/entities/library/model/tagTree';
import { useTemplateTagMutations } from '@/entities/template-spec/api/spec.queries';
import {
  TagManagerDialog,
  type ManagedTag,
} from '@/features/tag-manager/TagManagerDialog';

export interface TemplateTagManagerProps {
  /** 模板独立词表，由列表页容器传入；写操作后查询层统一失效 */
  tags: TagOut[];
  onClose: () => void;
}

/**
 * 模板标签管理：数据适配 + mutation。
 *
 * 与素材库标签管理共用 `TagManagerDialog`；这里只把 `specCount` 换成弹窗
 * 需要的数量，并从平铺数组算出「有没有下级」。模板词表与素材库词表仍是
 * 两套独立数据，写操作也各走各的接口。
 */
export function TemplateTagManager({ tags, onClose }: TemplateTagManagerProps) {
  const { create, rename, move, remove } = useTemplateTagMutations();
  const busy = create.isPending || rename.isPending || move.isPending || remove.isPending;

  const stats = useMemo(() => tags as unknown as LibraryTagStatOut[], [tags]);
  const managed = useMemo<ManagedTag[]>(
    () =>
      tags.map((tag) => ({
        id: tag.id,
        name: tag.name,
        parentId: tag.parentId,
        parentName: tag.parentName,
        depth: tag.depth,
        assetCount: 0,
        count: tag.specCount ?? 0,
        childCount: descendantIds(stats, tag.id).size,
        createdAt: '',
      })),
    [tags, stats],
  );

  return (
    <TagManagerDialog
      title="管理模板标签"
      rootLabel="一级维度"
      countUnit="个模板"
      tags={managed}
      busy={busy}
      onClose={onClose}
      onCreate={async ({ name, parentId }) => {
        await create.mutateAsync({ name, parentId });
      }}
      onRename={async (id, name) => {
        await rename.mutateAsync({ id, payload: { name } });
      }}
      onMove={async (id, parentId) => {
        await move.mutateAsync({ id, payload: { parentId } });
      }}
      onDelete={async (id) => {
        await remove.mutateAsync(id);
      }}
    />
  );
}