import { useEffect, useMemo, useState } from 'react';
import { Trash2 } from 'lucide-react';
import { Button, Modal } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import { sortByCreated, topLevelTags } from '@/entities/library/model/tagTree';
import {
  useCreateTag,
  useDeleteTag,
  useMoveTag,
  useRenameTag,
} from '@/entities/library/api/library.queries';

export interface LibraryTagManagerProps {
  /** 整个词表（含计数），由资产库容器传入；写操作后查询层统一失效 */
  tags: LibraryTagStatOut[];
  onClose: () => void;
}

/**
 * 标签词表管理：建、改名、移动层级、删除。
 *
 * 后端本来就有 `POST/PATCH/DELETE /library/tags`，但界面只接了「新建」。
 * 两级分类要能真正用起来，必须有地方把「3030」挂到「产品」下面，否则
 * 只能靠运维命令改库。这个弹窗只在 `library:tag:manage` 权限下出现。
 */
export function LibraryTagManager({ tags, onClose }: LibraryTagManagerProps) {
  const [newName, setNewName] = useState('');
  const [newParent, setNewParent] = useState('');
  const create = useCreateTag();
  const rename = useRenameTag();
  const move = useMoveTag();
  const del = useDeleteTag();
  const busy = create.isPending || rename.isPending || move.isPending || del.isPending;

  const tops = useMemo(() => topLevelTags(tags), [tags]);
  const ordered = useMemo(() => {
    const result: LibraryTagStatOut[] = [];
    for (const top of tops) {
      result.push(top);
      result.push(...sortByCreated(tags.filter((tag) => tag.parentId === top.id)));
    }
    const seen = new Set(result.map((tag) => tag.id));
    result.push(...sortByCreated(tags.filter((tag) => !seen.has(tag.id))));
    return result;
  }, [tags, tops]);

  const fail = (error: unknown) => toast(error instanceof Error ? error.message : '操作失败', 'error');

  async function submitCreate() {
    const name = newName.trim();
    if (!name || busy) return;
    try {
      await create.mutateAsync({ name, parentId: newParent || undefined });
      setNewName('');
      toast('已创建标签');
    } catch (error) {
      fail(error);
    }
  }

  async function doRename(id: string, name: string) {
    try {
      await rename.mutateAsync({ id, name });
      toast('已改名');
    } catch (error) {
      fail(error);
    }
  }

  async function doMove(id: string, parentId: string | null) {
    try {
      await move.mutateAsync({ id, parentId });
      toast('已调整层级');
    } catch (error) {
      fail(error);
    }
  }

  async function doDelete(id: string) {
    try {
      await del.mutateAsync(id);
      toast('已删除标签');
    } catch (error) {
      fail(error);
    }
  }

  return (
    <Modal open onClose={onClose} width={580} title="管理标签（一级 / 二级）">
      <div className="space-y-3 p-[18px]">
        <p className="text-[13.5px] leading-5 text-ink-mute">
          一级是分组，旗下二级才是打在素材上的系列。删除一级会把它旗下的二级提升为一级，资产与已有绑定不受影响。
        </p>

        <div className="max-h-[360px] space-y-1 overflow-auto rounded-lg border border-line-soft bg-panel-soft/40 p-2">
          {ordered.length === 0 && (
            <p className="px-1 py-6 text-center text-[13.5px] text-ink-mute">还没有标签</p>
          )}
          {ordered.map((tag) => (
            <ManagerRow
              key={tag.id}
              tag={tag}
              tops={tops}
              busy={busy}
              onRename={doRename}
              onMove={doMove}
              onDelete={doDelete}
            />
          ))}
        </div>

        <div className="flex items-center gap-1.5 border-t border-line-soft pt-3">
          <select
            value={newParent}
            aria-label="新标签所属一级"
            disabled={busy}
            onChange={(event) => setNewParent(event.target.value)}
            className="h-8 w-[128px] shrink-0 rounded-lg border border-line bg-white px-1.5 text-[13px] text-ink focus:border-primary-line focus:outline-none disabled:bg-panel-soft"
          >
            <option value="">作为一级标签</option>
            {tops.map((tag) => (
              <option key={tag.id} value={tag.id}>
                {tag.name} 下
              </option>
            ))}
          </select>
          <input
            type="text"
            value={newName}
            maxLength={40}
            disabled={busy}
            placeholder="新标签名称"
            aria-label="新标签名称"
            onChange={(event) => setNewName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== 'Enter') return;
              event.preventDefault();
              void submitCreate();
            }}
            className="h-8 min-w-0 flex-1 rounded-lg border border-line bg-white px-2.5 text-[14px] text-ink placeholder:text-ink-mute focus:border-primary-line focus:outline-none disabled:bg-panel-soft"
          />
          <Button
            size="sm"
            variant="primary"
            disabled={!newName.trim() || busy}
            onClick={() => void submitCreate()}
          >
            新建
          </Button>
        </div>
      </div>
    </Modal>
  );
}

interface ManagerRowProps {
  tag: LibraryTagStatOut;
  tops: LibraryTagStatOut[];
  busy: boolean;
  onRename: (id: string, name: string) => Promise<void>;
  onMove: (id: string, parentId: string | null) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

function ManagerRow({ tag, tops, busy, onRename, onMove, onDelete }: ManagerRowProps) {
  const [name, setName] = useState(tag.name);
  const [confirming, setConfirming] = useState(false);
  useEffect(() => setName(tag.name), [tag.name]);

  const hasChildren = (tag.childCount ?? 0) > 0;
  const indent = tag.parentId ? 'ml-5 border-l-2 border-primary-line pl-2' : '';
  const selectDisabled = busy || hasChildren;

  return (
    <div className={`flex flex-wrap items-center gap-1.5 rounded-md bg-panel px-2 py-1.5 ${indent}`}>
      <span className="w-9 shrink-0 text-[12.5px] text-ink-mute">
        {tag.parentId ? '二级' : '一级'}
      </span>
      <input
        type="text"
        value={name}
        maxLength={40}
        disabled={busy}
        aria-label={`${tag.name} 的名称`}
        onChange={(event) => setName(event.target.value)}
        className="h-7 min-w-[90px] flex-1 rounded-md border border-line-soft bg-white px-2 text-[13.5px] text-ink focus:border-primary-line focus:outline-none disabled:bg-panel-soft"
      />
      <Button
        size="sm"
        variant="ghost"
        disabled={busy || !name.trim() || name.trim() === tag.name}
        onClick={() => void onRename(tag.id, name.trim())}
      >
        改名
      </Button>
      <select
        value={tag.parentId ?? ''}
        aria-label={`${tag.name} 的层级`}
        disabled={selectDisabled}
        title={hasChildren ? '旗下已有二级，不能再挂到别的一级下' : '调整所属一级'}
        onChange={(event) => void onMove(tag.id, event.target.value || null)}
        className="h-7 w-[124px] shrink-0 rounded-md border border-line-soft bg-white px-1.5 text-[13px] text-ink focus:border-primary-line focus:outline-none disabled:bg-panel-soft"
      >
        <option value="">一级标签</option>
        {tops
          .filter((top) => top.id !== tag.id)
          .map((top) => (
            <option key={top.id} value={top.id}>
              {top.name} 下
            </option>
          ))}
      </select>
      <span className="shrink-0 text-[12.5px] tabular-nums text-ink-mute">
        {tag.assetCount ?? 0} 条
        {hasChildren ? ` · ${tag.childCount} 个二级` : ''}
      </span>
      {confirming ? (
        <span className="inline-flex shrink-0 items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            className="text-danger-deep"
            disabled={busy}
            onClick={() => {
              setConfirming(false);
              void onDelete(tag.id);
            }}
          >
            确认删除
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
            取消
          </Button>
        </span>
      ) : (
        <button
          type="button"
          aria-label={`删除标签 ${tag.name}`}
          disabled={busy}
          onClick={() => setConfirming(true)}
          className="grid size-[24px] shrink-0 place-items-center rounded-md text-ink-mute transition hover:bg-danger-soft hover:text-danger-deep disabled:opacity-40"
        >
          <Trash2 size={12} />
        </button>
      )}
    </div>
  );
}
