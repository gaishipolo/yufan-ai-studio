import { useMemo } from 'react';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import {
  useCreateTag,
  useDeleteTag,
  useMoveTag,
  useRenameTag,
} from '@/entities/library/api/library.queries';
import {
  TagManagerDialog,
  type ManagedTag,
} from '@/features/tag-manager/TagManagerDialog';

export interface LibraryTagManagerProps {
  /** 整个词表（含计数），由资产库容器传入；写操作后查询层统一失效 */
  tags: LibraryTagStatOut[];
  onClose: () => void;
}

/**
 * 素材库标签管理：数据适配 + mutation。
 *
 * 弹窗结构与模板封装那边完全一样，展开/收起、排序、孤儿保留、两段删除确认
 * 都在共享的 `TagManagerDialog` 里，避免两处各维护一份再漂移。
 */
export function LibraryTagManager({ tags, onClose }: LibraryTagManagerProps) {
  const create = useCreateTag();
  const rename = useRenameTag();
  const move = useMoveTag();
  const del = useDeleteTag();
  const busy = create.isPending || rename.isPending || move.isPending || del.isPending;

  const managed = useMemo<ManagedTag[]>(
    () => tags.map((tag) => ({ ...tag, count: tag.assetCount ?? 0 })),
    [tags],
  );

  return (
    <TagManagerDialog
      title="管理标签"
      rootLabel="一级标签"
      countUnit="条"
      tags={managed}
      busy={busy}
      onClose={onClose}
      onCreate={async ({ name, parentId }) => {
        await create.mutateAsync({ name, parentId });
      }}
      onRename={async (id, name) => {
        await rename.mutateAsync({ id, name });
      }}
      onMove={async (id, parentId) => {
        await move.mutateAsync({ id, parentId });
      }}
      onDelete={async (id) => {
        await del.mutateAsync(id);
      }}
    />
  );
}