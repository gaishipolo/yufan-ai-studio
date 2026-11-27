import { useEffect, useMemo, useState } from 'react';
import { Trash2 } from 'lucide-react';
import { Button, Modal } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import type { LibraryTagStatOut, TagOut } from '@/shared/api/schema';
import {
  descendantIds,
  levelLabel,
  sortByCreated,
  tagDepth,
  tagsAtDepth,
  topLevelTags,
} from '@/entities/library/model/tagTree';
import { useTemplateTagMutations } from '@/entities/template-spec/api/spec.queries';

/**
 * `TagOut` 与素材库的 `LibraryTagStatOut` 形状兼容（id/name/parentId/parentName/
 * depth），只是没有 `childCount` / `createdAt`。词表树的纯函数只依赖前几项，
 * 所以这里转换一次给它们用；「有没有下级」则从平铺数组自己算，不看 childCount。
 */
const asStats = (tags: TagOut[]) => tags as unknown as LibraryTagStatOut[];

export interface TemplateTagManagerProps {
  /** 模板独立词表，由列表页容器传入；写操作后查询层统一失效 */
  tags: TagOut[];
  onClose: () => void;
}

/**
 * 模板标签词表管理：建、改名、移动层级、删除。
 *
 * 与素材库的 `LibraryTagManager` 同一套两级模型（一级维度 → 二级取值），
 * 但用的是 `/template-specs/tags` 那套**独立**词表与 mutation —— 模块之间
 * 不互相 import 业务代码，只复用 entities/model 层的纯函数。
 *
 * 有下级的维度不能移动层级（否则它的取值会被挂到别处或提升成孤儿）；
 * 删除要两段确认，确认文案写清「下级会提升一级」这个后果。
 */
export function TemplateTagManager({ tags, onClose }: TemplateTagManagerProps) {
  const [newName, setNewName] = useState('');
  const [newParent, setNewParent] = useState('');
  const { create, rename, move, remove } = useTemplateTagMutations();
  const busy = create.isPending || rename.isPending || move.isPending || remove.isPending;

  const stats = useMemo(() => asStats(tags), [tags]);
  const tops = useMemo(() => topLevelTags(stats), [stats]);
  const ordered = useMemo(() => {
    const result: TagOut[] = [];
    const seen = new Set<string>();
    const seconds = tagsAtDepth(stats, 2) as unknown as TagOut[];
    for (const top of tops as unknown as TagOut[]) {
      result.push(top);
      seen.add(top.id);
      for (const second of seconds.filter((tag) => tag.parentId === top.id)) {
        result.push(second);
        seen.add(second.id);
      }
    }
    // 父标签不在词表里的孤儿放到最后，别让它在树里凭空消失
    result.push(...(sortByCreated(stats.filter((tag) => !seen.has(tag.id))) as unknown as TagOut[]));
    return result;
  }, [stats, tops]);

  /** 新建时的可选上级：只有一级维度（取值只能挂在维度下面）。 */
  const parents = useMemo(
    () => tops.map((tag) => ({ id: tag.id, label: tag.name })),
    [tops],
  );

  const fail = (error: unknown) =>
    toast(error instanceof Error ? error.message : '操作失败', 'error');

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
      await rename.mutateAsync({ id, payload: { name } });
      toast('已改名');
    } catch (error) {
      fail(error);
    }
  }

  async function doMove(id: string, parentId: string | null) {
    try {
      await move.mutateAsync({ id, payload: { parentId } });
      toast('已调整层级');
    } catch (error) {
      fail(error);
    }
  }

  async function doDelete(id: string) {
    try {
      await remove.mutateAsync(id);
      toast('已删除标签');
    } catch (error) {
      fail(error);
    }
  }

  return (
    <Modal open onClose={onClose} width={580} title="管理模板标签">
      <div className="space-y-3 p-[18px]">
        <div className="max-h-[360px] space-y-1 overflow-auto rounded-lg border border-line-soft bg-panel-soft/40 p-2">
          {ordered.length === 0 && (
            <p className="px-1 py-6 text-center text-body text-ink-mute">还没有标签</p>
          )}
          {ordered.map((tag) => (
            <ManagerRow
              key={tag.id}
              tag={tag}
              stats={stats}
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
            aria-label="新标签所属上级"
            disabled={busy}
            onChange={(event) => setNewParent(event.target.value)}
            className="h-8 w-[128px] shrink-0 rounded-lg border border-line bg-panel px-1.5 text-body text-ink focus:border-primary-line focus:outline-none disabled:bg-panel-soft"
          >
            <option value="">作为一级维度</option>
            {parents.map((parent) => (
              <option key={parent.id} value={parent.id}>
                {parent.label} 下
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
            className="h-8 min-w-0 flex-1 rounded-lg border border-line bg-panel px-2.5 text-body text-ink placeholder:text-ink-mute focus:border-primary-line focus:outline-none focus:ring-2 focus:ring-primary/10 disabled:bg-panel-soft"
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
  tag: TagOut;
  /** 整个词表：算层级与可选上级都要用 */
  stats: LibraryTagStatOut[];
  busy: boolean;
  onRename: (id: string, name: string) => Promise<void>;
  onMove: (id: string, parentId: string | null) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

function ManagerRow({ tag, stats, busy, onRename, onMove, onDelete }: ManagerRowProps) {
  const [name, setName] = useState(tag.name);
  const [confirming, setConfirming] = useState(false);
  useEffect(() => setName(tag.name), [tag.name]);

  const depth = tagDepth(stats, tag);
  const children = useMemo(() => descendantIds(stats, tag.id), [stats, tag.id]);
  const hasChildren = children.size > 0;
  const indent = depth === 1 ? '' : 'ml-5 border-l-2 border-primary-line pl-2';
  const selectDisabled = busy || hasChildren;

  /* 可选上级：只有一级维度；自己和下级都要排掉（挂到自己下级下面是非法的）。 */
  const moveParents = useMemo(
    () =>
      topLevelTags(stats)
        .filter((item) => item.id !== tag.id && !children.has(item.id))
        .map((item) => ({ id: item.id, label: item.name })),
    [stats, tag.id, children],
  );

  return (
    <div className={`flex flex-wrap items-center gap-1.5 rounded-md bg-panel px-2 py-1.5 ${indent}`}>
      <span className="w-9 shrink-0 text-xs text-ink-mute">{levelLabel(depth)}</span>
      <input
        type="text"
        value={name}
        maxLength={40}
        disabled={busy}
        aria-label={`${tag.name} 的名称`}
        onChange={(event) => setName(event.target.value)}
        className="h-7 min-w-[90px] flex-1 rounded-md border border-line-soft bg-panel px-2 text-body text-ink focus:border-primary-line focus:outline-none disabled:bg-panel-soft"
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
        title={hasChildren ? '旗下已有下级，不能再挂到别处' : '调整所属上级'}
        onChange={(event) => void onMove(tag.id, event.target.value || null)}
        className="h-7 w-[124px] shrink-0 rounded-md border border-line-soft bg-panel px-1.5 text-body text-ink focus:border-primary-line focus:outline-none disabled:bg-panel-soft"
      >
        <option value="">一级维度</option>
        {moveParents.map((parent) => (
          <option key={parent.id} value={parent.id}>
            {parent.label} 下
          </option>
        ))}
      </select>
      <span className="shrink-0 text-xs tabular-nums text-ink-mute">
        {tag.specCount ?? 0} 个模板
        {hasChildren ? ` · ${children.size} 个${levelLabel(depth + 1)}` : ''}
      </span>
      {confirming ? (
        <span className="inline-flex shrink-0 items-center gap-1">
          <span className="text-xs text-danger-deep">
            {hasChildren ? `删除后 ${children.size} 个下级会提升一级` : '删除后不可恢复'}
          </span>
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
