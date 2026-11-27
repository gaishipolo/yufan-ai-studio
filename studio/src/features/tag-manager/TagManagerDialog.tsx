import { useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Trash2 } from 'lucide-react';
import { Button, Modal } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import {
  levelLabel,
  sortByCreated,
  tagDepth,
  tagsAtDepth,
  topLevelTags,
} from '@/entities/library/model/tagTree';

/** 管理弹窗需要的统一形状：素材库与模板词表各自适配出这一个。 */
export interface ManagedTag extends LibraryTagStatOut {
  /** 弹窗里显示的数量（素材条数 / 模板个数） */
  count: number;
}

export interface TagManagerDialogProps {
  title: string;
  /** 一级的称呼：「一级标签」/「一级维度」 */
  rootLabel: string;
  /** 数量的单位：「条」/「个模板」 */
  countUnit: string;
  tags: ManagedTag[];
  busy: boolean;
  onClose: () => void;
  onCreate: (payload: { name: string; parentId?: string }) => Promise<void>;
  onRename: (id: string, name: string) => Promise<void>;
  onMove: (id: string, parentId: string | null) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

/**
 * 两级标签词表的共用管理弹窗。
 *
 * 素材库与模板封装的弹窗结构完全一样，差别只有：标题、一级的叫法、数量单位、
 * 数据源和 mutation。这些由调用方适配；展开/收起、排序、孤儿保留、两段删除
 * 确认只在这里写一次，避免两边再漂移。
 *
 * **默认全部收起**：一级多、二级更多时，平铺会把弹窗拉得很长。新建二级后自动
 * 展开它的父级，避免“创建成功但列表里看不见”。
 */
export function TagManagerDialog({
  title,
  rootLabel,
  countUnit,
  tags,
  busy,
  onClose,
  onCreate,
  onRename,
  onMove,
  onDelete,
}: TagManagerDialogProps) {
  const [newName, setNewName] = useState('');
  const [newParent, setNewParent] = useState('');
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

  const tops = useMemo(() => topLevelTags(tags) as ManagedTag[], [tags]);
  const topIds = useMemo(() => new Set(tops.map((tag) => tag.id)), [tops]);
  const ordered = useMemo(() => {
    const result: ManagedTag[] = [];
    const seen = new Set<string>();
    const seconds = tagsAtDepth(tags, 2) as ManagedTag[];
    for (const top of tops) {
      result.push(top);
      seen.add(top.id);
      for (const second of seconds.filter((tag) => tag.parentId === top.id)) {
        result.push(second);
        seen.add(second.id);
      }
    }
    // 父标签不在词表里的孤儿放到最后，别让它在树里凭空消失
    result.push(...(sortByCreated(tags) as ManagedTag[]).filter((tag) => !seen.has(tag.id)));
    return result;
  }, [tags, tops]);

  const parents = useMemo(
    () => tops.map((tag) => ({ id: tag.id, label: tag.name })),
    [tops],
  );

  const toggleExpanded = (id: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const fail = (error: unknown) =>
    toast(error instanceof Error ? error.message : '操作失败', 'error');

  async function submitCreate() {
    const name = newName.trim();
    if (!name || busy) return;
    try {
      await onCreate({ name, parentId: newParent || undefined });
      setNewName('');
      if (newParent) setExpanded((current) => new Set(current).add(newParent));
      toast('已创建标签');
    } catch (error) {
      fail(error);
    }
  }

  async function doRename(id: string, name: string) {
    try {
      await onRename(id, name);
      toast('已改名');
    } catch (error) {
      fail(error);
    }
  }

  async function doMove(id: string, parentId: string | null) {
    try {
      await onMove(id, parentId);
      toast('已调整层级');
    } catch (error) {
      fail(error);
    }
  }

  async function doDelete(id: string) {
    try {
      await onDelete(id);
      toast('已删除标签');
    } catch (error) {
      fail(error);
    }
  }

  return (
    <Modal open onClose={onClose} width={580} title={title}>
      <div className="space-y-3 p-[18px]">
        <div className="max-h-[360px] space-y-1 overflow-auto rounded-lg border border-line-soft bg-panel-soft/40 p-2">
          {ordered.length === 0 && (
            <p className="px-1 py-6 text-center text-body text-ink-mute">还没有标签</p>
          )}
          {ordered.map((tag) => {
            if (tag.parentId && topIds.has(tag.parentId) && !expanded.has(tag.parentId)) {
              return null;
            }
            return (
              <ManagerRow
                key={tag.id}
                tag={tag}
                tags={tags}
                countUnit={countUnit}
                rootLabel={rootLabel}
                busy={busy}
                expanded={expanded.has(tag.id)}
                onToggle={() => toggleExpanded(tag.id)}
                onRename={doRename}
                onMove={doMove}
                onDelete={doDelete}
              />
            );
          })}
        </div>

        <div className="flex items-center gap-1.5 border-t border-line-soft pt-3">
          <select
            value={newParent}
            aria-label="新标签所属上级"
            disabled={busy}
            onChange={(event) => setNewParent(event.target.value)}
            className="h-8 w-[128px] shrink-0 rounded-lg border border-line bg-panel px-1.5 text-body text-ink focus:border-primary-line focus:outline-none disabled:bg-panel-soft"
          >
            <option value="">作为{rootLabel}</option>
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
  tag: ManagedTag;
  tags: ManagedTag[];
  countUnit: string;
  rootLabel: string;
  busy: boolean;
  expanded: boolean;
  onToggle: () => void;
  onRename: (id: string, name: string) => Promise<void>;
  onMove: (id: string, parentId: string | null) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

function ManagerRow({
  tag,
  tags,
  countUnit,
  rootLabel,
  busy,
  expanded,
  onToggle,
  onRename,
  onMove,
  onDelete,
}: ManagerRowProps) {
  const [name, setName] = useState(tag.name);
  const [confirming, setConfirming] = useState(false);
  useEffect(() => setName(tag.name), [tag.name]);

  const depth = tagDepth(tags, tag);
  const hasChildren = (tag.childCount ?? 0) > 0;
  const indent = depth === 1 ? '' : 'ml-5 border-l-2 border-primary-line pl-2';
  const selectDisabled = busy || hasChildren;

  /* 可选上级：只有一级维度；自己的下级要排掉（挂到自己下级下面是非法的） */
  const forbidden = useMemo(
    () => new Set(tags.filter((item) => item.parentId === tag.id).map((item) => item.id)),
    [tags, tag.id],
  );
  const moveParents = useMemo(
    () =>
      topLevelTags(tags)
        .filter((item) => item.id !== tag.id && !forbidden.has(item.id))
        .map((item) => ({ id: item.id, label: item.name })),
    [tags, tag.id, forbidden],
  );

  return (
    <div className={`flex flex-wrap items-center gap-1.5 rounded-md bg-panel px-2 py-1.5 ${indent}`}>
      {depth === 1 && hasChildren ? (
        <button
          type="button"
          aria-label={`${expanded ? '收起' : '展开'} ${tag.name}`}
          aria-expanded={expanded}
          onClick={onToggle}
          className="grid size-6 shrink-0 place-items-center rounded-md text-ink-mute transition-colors hover:bg-panel-soft hover:text-ink"
        >
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
      ) : depth === 1 ? (
        <span className="size-6 shrink-0" aria-hidden />
      ) : null}
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
        <option value="">{rootLabel}</option>
        {moveParents.map((parent) => (
          <option key={parent.id} value={parent.id}>
            {parent.label} 下
          </option>
        ))}
      </select>
      <span className="shrink-0 text-xs tabular-nums text-ink-mute">
        {tag.count} {countUnit}
        {hasChildren ? ` · ${tag.childCount} 个${levelLabel(depth + 1)}` : ''}
      </span>
      {confirming ? (
        <span className="inline-flex shrink-0 items-center gap-1">
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