import { useMemo, useState } from 'react';
import { X } from 'lucide-react';
import { useAssetViews, useSaveAssetView, useDeleteAssetView, useTagCatalog } from '@/entities/asset/api/tag.queries';
import type { TagOut } from '@/shared/api/schema';
import { Button, SearchInput } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';

/**
 * 第③段：筛选条（受控标签）。
 *
 * 三条硬约束写在这里，因为它们都容易被"顺手优化"掉：
 *
 * 1. **只能选词表里的词**。这个组件没有任何"新建标签"入口，输入的搜索框只在
 *    词表内模糊匹配。落地方式不是"不给输入框"，而是接口只接受 id——
 *    前端那个框是"找词"，不是"造词"。
 * 2. **多选是 AND**。选中三项 = "同时具备"，与后端 `HAVING COUNT(*) = N` 对齐。
 *    chip 上不要写"或"，那会让人以为结果变宽了。
 * 3. **状态放 URL，不放 localStorage**。放 URL 才能分享、能后退、能贴进工单；
 *    localStorage 的筛选是一份"只有这台机器知道"的状态，刷新一下像是丢了。
 *
 * 常显区只放 `pinned` 的词（用满 5 次），其余收进"＋更多"：词表会增长，
 * 而筛选条是固定宽度的界面，让它随词表增长等于让界面不可用。
 */
export function TagFilterBar({
  selected,
  onChange,
  savedQuery,
}: {
  selected: string[];
  onChange: (next: string[]) => void;
  /** 当前全部筛选参数，保存视图时原样存下来（存参数，不存结果） */
  savedQuery: Record<string, unknown>;
}) {
  const {data: catalog} = useTagCatalog();
  const {data: views = []} = useAssetViews();
  const saveView = useSaveAssetView();
  const deleteView = useDeleteAssetView();
  const [open, setOpen] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [viewName, setViewName] = useState('');

  // `?? []` 每次渲染都是新数组，直接进 useMemo 依赖会让 `byId` 白算一遍
  const groups = useMemo(() => catalog?.groups ?? [], [catalog?.groups]);
  const threshold = catalog?.pinnedThreshold ?? 5;

  /** `id → 词`，chip 与按钮都要按 id 反查名字；词表不大，一次建表比反复 find 便宜。 */
  const byId = useMemo(() => {
    const map = new Map<string, TagOut>();
    for (const group of groups) {
      for (const tag of group.items ?? []) map.set(tag.id, tag);
    }
    return map;
  }, [groups]);

  const toggle = (id: string) => {
    onChange(selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id]);
  };

  const matched = (tag: TagOut) => !keyword.trim() || tag.name.includes(keyword.trim());

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-xs text-ink-mute">标签</span>
        {selected.length === 0 && <span className="text-xs text-ink-mute">未筛选（多选为「同时具备」）</span>}
        {selected.map((id) => (
          <button
            key={id}
            type="button"
            onClick={() => toggle(id)}
            className="inline-flex h-6 items-center gap-1 rounded-pill border border-primary-line bg-primary-soft px-2 text-[11px] text-primary-deep"
            aria-label={`移除标签 ${byId.get(id)?.name ?? id}`}
          >
            {byId.get(id)?.name ?? id}
            <X size={11} aria-hidden />
          </button>
        ))}
        <Button size="sm" variant="secondary" onClick={() => setOpen((value) => !value)}>
          {open ? '收起标签' : '＋选标签'}
        </Button>
        <SavedViews
          views={views}
          onApply={(query) => onChange(Array.isArray(query.tagIds) ? (query.tagIds as string[]) : [])}
          onDelete={(id) => deleteView.mutate(id)}
        />
        <div className="ml-auto flex items-center gap-1.5">
          <input
            className="h-6 w-28 rounded border border-line bg-panel px-1.5 text-[11px]"
            placeholder="视图名"
            value={viewName}
            onChange={(event) => setViewName(event.target.value)}
            aria-label="保存视图名"
          />
          <Button
            size="sm"
            variant="secondary"
            disabled={!viewName.trim() || saveView.isPending}
            onClick={() =>
              saveView.mutate(
                {name: viewName.trim(), query: savedQuery},
                {onSuccess: () => setViewName('')},
              )
            }
          >
            保存视图
          </Button>
        </div>
      </div>

      {open && (
        <div className="space-y-2 rounded-lg border border-line bg-panel p-2">
          <SearchInput
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="在词表内搜索（不是新建）"
            aria-label="标签搜索"
          />
          {groups.map((group) => {
            const items = (group.items ?? []).filter(matched);
            if (!items.length) return null;
            // 常显词在前，其余收进「更多」——阈值来自接口，前端不硬编码
            const pinned = items.filter((tag) => tag.pinned === true);
            const rest = items.filter((tag) => tag.pinned !== true);
            return (
              <div key={group.dimension} className="flex flex-wrap items-center gap-1">
                <span className="w-16 shrink-0 text-[11px] text-ink-mute">{group.label}</span>
                {pinned.map((tag) => (
                  <TagButton key={tag.id} tag={tag} active={selected.includes(tag.id)} onClick={() => toggle(tag.id)} />
                ))}
                {/* 搜索时不过滤掉低频词：用户既然在找，就该找得到；
                    不搜索时才把它们收进「更多」，让常显区保持固定宽度 */}
                {keyword.trim() &&
                  rest.map((tag) => (
                    <TagButton key={tag.id} tag={tag} active={selected.includes(tag.id)} onClick={() => toggle(tag.id)} />
                  ))}
                {!keyword.trim() && rest.length > 0 && (
                  <details className="inline-block">
                    <summary className="cursor-pointer text-[11px] text-ink-mute">
                      ＋更多（{rest.length}）
                    </summary>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {rest.map((tag) => (
                        <TagButton key={tag.id} tag={tag} active={selected.includes(tag.id)} onClick={() => toggle(tag.id)} />
                      ))}
                    </div>
                  </details>
                )}
              </div>
            );
          })}
          <p className="text-[11px] text-ink-mute">
            用满 {threshold} 次的词才进常显区；新词可以先在「＋更多」里用。
          </p>
        </div>
      )}
    </div>
  );
}

function TagButton({tag, active, onClick}: {tag: TagOut; active: boolean; onClick: () => void}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        'inline-flex h-6 items-center gap-1 rounded-pill border px-2 text-[11px]',
        active
          ? 'border-primary-line bg-primary-soft font-semibold text-primary-deep'
          : 'border-line bg-panel text-ink-soft hover:border-line-strong',
      )}
    >
      {tag.name}
      {typeof tag.useCount === 'number' && tag.useCount > 0 && (
        <span className="text-[10px] tabular-nums text-ink-mute">{tag.useCount}</span>
      )}
    </button>
  );
}

/**
 * 保存视图。
 *
 * 恢复动作是「把参数放回去」，不是「把结果固定下来」——所以这里的回调只交还
 * `query`，由父组件重新发起一次查询。存结果集的视图会在素材增减后给出过期列表，
 * 而没人知道它是哪个时刻的快照。
 */
function SavedViews({
  views,
  onApply,
  onDelete,
}: {
  views: Array<{id: string; name: string; query?: Record<string, unknown>}>;
  onApply: (query: Record<string, unknown>) => void;
  onDelete: (id: string) => void;
}) {
  if (!views.length) return null;
  return (
    <span className="flex flex-wrap items-center gap-1">
      {views.map((view) => (
        <span
          key={view.id}
          className="inline-flex h-6 items-center rounded-pill border border-line bg-panel-soft text-[11px] text-ink-soft"
        >
          <button type="button" className="px-2" onClick={() => onApply(view.query ?? {})}>
            {view.name}
          </button>
          <button
            type="button"
            className="px-1 text-ink-mute hover:text-danger"
            aria-label={`删除视图 ${view.name}`}
            onClick={() => onDelete(view.id)}
          >
            <X size={11} aria-hidden />
          </button>
        </span>
      ))}
    </span>
  );
}
