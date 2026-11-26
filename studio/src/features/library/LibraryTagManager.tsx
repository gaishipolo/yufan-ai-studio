import { useEffect, useMemo, useState } from 'react';
import { Trash2 } from 'lucide-react';
import { Button, Modal } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import {
  descendantIds,
  levelLabel,
  sortByCreated,
  tagDepth,
  tagsAtDepth,
  topLevelTags,
} from '@/entities/library/model/tagTree';
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
 * 分类要能真正用起来，必须有地方把「6036」挂到「产品」下面，否则
 * 只能靠运维命令改库。这个弹窗只在 `library:tag:manage` 权限下出现。
 *
 * 树最多**两级**，列表按「一级维度 → 二级取值」原序排下来，层级字面量按
 * 真实层数给，不从「有没有父」硬推。
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
    const seen = new Set<string>();
    const seconds = tagsAtDepth(tags, 2);
    for (const top of tops) {
      result.push(top);
      seen.add(top.id);
      for (const second of seconds.filter((tag) => tag.parentId === top.id)) {
        result.push(second);
        seen.add(second.id);
      }
    }
    // 父标签不在词表里的孤儿单独放到最后，别让它在树里凭空消失
    result.push(...sortByCreated(tags.filter((tag) => !seen.has(tag.id))));
    return result;
  }, [tags, tops]);

  /** 新建时的可选上级：**只有一级维度**（取值只能挂在维度下面） */
  const parents = useMemo(
    () => tops.map((tag) => ({ id: tag.id, label: tag.name })),
    [tops],
  );

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
    <Modal open onClose={onClose} width={580} title="管理标签">
      <div className="space-y-3 p-[18px]">
        {/* 原来三句：前两句是**字段含义**（列表每行左侧的层级标签已经表达了），
            第三句是**危险操作的后果** —— 该留，但它离删除按钮很远。
            后果挪到删除确认处（见 ManagerRow 的删除确认）。 */}

        <div className="max-h-[360px] space-y-1 overflow-auto rounded-lg border border-line-soft bg-panel-soft/40 p-2">
          {ordered.length === 0 && (
            <p className="px-1 py-6 text-center text-body text-ink-mute">还没有标签</p>
          )}
          {ordered.map((tag) => (
            <ManagerRow
              key={tag.id}
              tag={tag}
              tags={tags}
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
            <option value="">作为一级标签</option>
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
  tag: LibraryTagStatOut;
  /** 整个词表：算层级与可选上级都要用 */
  tags: LibraryTagStatOut[];
  busy: boolean;
  onRename: (id: string, name: string) => Promise<void>;
  onMove: (id: string, parentId: string | null) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

function ManagerRow({ tag, tags, busy, onRename, onMove, onDelete }: ManagerRowProps) {
  const [name, setName] = useState(tag.name);
  const [confirming, setConfirming] = useState(false);
  useEffect(() => setName(tag.name), [tag.name]);

  const depth = tagDepth(tags, tag);
  const hasChildren = (tag.childCount ?? 0) > 0;
  const indent =
    depth === 1 ? '' : 'ml-5 border-l-2 border-primary-line pl-2';
  const selectDisabled = busy || hasChildren;

  /* 可选上级：只有一级维度；自己的下级要排掉（挂到自己下级下面是非法的） */
  const forbidden = useMemo(() => descendantIds(tags, tag.id), [tags, tag.id]);
  const moveParents = useMemo(
    () =>
      topLevelTags(tags)
        .filter((item) => item.id !== tag.id && !forbidden.has(item.id))
        .map((item) => ({ id: item.id, label: item.name })),
    [tags, tag.id, forbidden],
  );

  return (
    <div className={`flex flex-wrap items-center gap-1.5 rounded-md bg-panel px-2 py-1.5 ${indent}`}>
      <span className="w-9 shrink-0 text-xs text-ink-mute">
        {levelLabel(depth)}
      </span>
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
        <option value="">一级标签</option>
        {moveParents.map((parent) => (
          <option key={parent.id} value={parent.id}>
            {parent.label} 下
          </option>
        ))}
      </select>
      <span className="shrink-0 text-xs tabular-nums text-ink-mute">
        {tag.assetCount ?? 0} 条
        {hasChildren ? ` · ${tag.childCount} 个${levelLabel(depth + 1)}` : ''}
      </span>
      {confirming ? (
        <span className="inline-flex shrink-0 items-center gap-1">
          {/* 删除的后果（会连带提升下级）只在这一刻说 ——
              原来那句话挂在弹窗顶部，离删除按钮很远，用户点到这里时已经忘了。 */}
          <span className="text-xs text-danger-deep">
            {hasChildren ? `删除后 ${tag.childCount} 个下级会提升一级` : '删除后不可恢复'}
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
