import { useEffect, useMemo, useState } from 'react';
import { Check, Library } from 'lucide-react';
import { Button, Chip, CoverImage, Modal, SearchInput, Select, Skeleton } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { useDebouncedValue } from '@/shared/hooks/useDebouncedValue';
import type { LibraryAssetKind, LibraryAssetOut } from '@/shared/api/schema';
import { useLibrarySelection } from '../api/library.queries';
import { KIND_LABEL, displayName } from '../model/types';

export interface LibraryPickerProps {
  open: boolean;
  title?: string;
  /** 单选用于「挑一张参考图」，多选用于「参考素材列表」 */
  multiple?: boolean;
  /** 只让某几类出现在候选里；不传 = 全部（封装除外） */
  kinds?: LibraryAssetKind[];
  /** 已选 id：打开时回显 */
  value?: string[];
  onClose: () => void;
  onConfirm: (assets: LibraryAssetOut[]) => void;
}

/**
 * 资产库选择器：给模板库 / 生图 / 工作台挑素材用。
 *
 * 只打 `GET /library/selection`（硬过滤 `state=active`）：草稿与回收站里的东西
 * 绝不能进生成任务，所以这不是一个可以从前端关掉的筛选参数。
 *
 * 选中项按 **id → 资产** 存在本地：确认时要把名称一起带回去（调用方要显示
 * 「已选 3 项：主视觉、产品图…」），只回 id 就得再查一次。
 */
export function LibraryPicker({
  open,
  title = '从资产库选择',
  multiple = true,
  kinds,
  value,
  onClose,
  onConfirm,
}: LibraryPickerProps) {
  const [keyword, setKeyword] = useState('');
  const [kind, setKind] = useState('');
  const [page, setPage] = useState(1);
  const [picked, setPicked] = useState<Record<string, LibraryAssetOut>>({});
  const debounced = useDebouncedValue(keyword, 250);

  const query = useLibrarySelection({
    keyword: debounced || undefined,
    kind: kind || undefined,
    page,
    pageSize: 24,
  });

  useEffect(() => {
    if (!open) return;
    setKeyword('');
    setKind('');
    setPage(1);
    setPicked({});
  }, [open]);

  const items = useMemo(() => {
    const list = query.data?.items ?? [];
    if (!kinds?.length) return list.filter((item) => item.kind !== 'pack');
    return list.filter((item) => kinds.includes(item.kind));
  }, [query.data?.items, kinds]);

  /* 已选 id 里可能有不在当前页的条目：等它们出现在结果里时补进 picked，
     这样「确认」返回的名称不会丢；找不到的（已删/不可见）就没法带了。 */
  useEffect(() => {
    if (!value?.length) return;
    setPicked((previous) => {
      const next = { ...previous };
      for (const item of items) {
        if (value.includes(item.id) && !next[item.id]) next[item.id] = item;
      }
      return next;
    });
  }, [items, value]);

  const chosen = Object.values(picked);

  const toggle = (asset: LibraryAssetOut) => {
    setPicked((previous) => {
      if (previous[asset.id]) {
        const next = { ...previous };
        delete next[asset.id];
        return next;
      }
      return multiple ? { ...previous, [asset.id]: asset } : { [asset.id]: asset };
    });
  };

  const totalPages = Math.max(1, Math.ceil((query.data?.total ?? 0) / 24));

  return (
    <Modal open={open} onClose={onClose} title={title} width={760}>
      <div className="space-y-3 p-[18px]">
        <div className="flex flex-wrap items-center gap-2">
          <div className="w-[220px]">
            <SearchInput
              value={keyword}
              onChange={(event) => {
                setKeyword(event.target.value);
                setPage(1);
              }}
              placeholder="搜索名称…"
              aria-label="搜索素材"
            />
          </div>
          <div className="w-[120px]">
            <Select
              options={[
                { value: '', label: '全部类型' },
                ...(kinds ?? (['video', 'image', 'audio'] as LibraryAssetKind[])).map((item) => ({
                  value: item,
                  label: KIND_LABEL[item],
                })),
              ]}
              value={kind}
              onChange={(event) => {
                setKind(event.target.value);
                setPage(1);
              }}
              aria-label="类型筛选"
            />
          </div>
          <Chip tone={chosen.length ? 'primary' : 'default'}>已选 {chosen.length}</Chip>
          <span className="ml-auto text-[11px] text-ink-mute">只显示在库素材</span>
        </div>

        {query.isPending ? (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(120px,1fr))] gap-2">
            {Array.from({ length: 8 }).map((_, index) => (
              <Skeleton key={index} className="h-[112px]" />
            ))}
          </div>
        ) : items.length ? (
          <div className="grid max-h-[340px] grid-cols-[repeat(auto-fill,minmax(120px,1fr))] gap-2 overflow-auto pr-1">
            {items.map((asset) => {
              const active = !!picked[asset.id];
              return (
                <button
                  key={asset.id}
                  type="button"
                  onClick={() => toggle(asset)}
                  aria-pressed={active}
                  className={cn(
                    'relative rounded-lg border p-1.5 text-left transition',
                    active
                      ? 'border-primary bg-primary-soft/40 ring-2 ring-primary/15'
                      : 'border-line-soft bg-panel hover:border-line-strong',
                  )}
                >
                  <CoverImage src={asset.coverUrl ?? undefined} seed={asset.id} className="aspect-[4/3] rounded-md" />
                  <div className="mt-1 truncate text-[11.5px] text-ink">{displayName(asset.name)}</div>
                  <div className="truncate text-[10.5px] text-ink-mute">{KIND_LABEL[asset.kind]}</div>
                  {active && (
                    <span className="absolute right-1.5 top-1.5 grid size-[18px] place-items-center rounded-full bg-primary text-white">
                      <Check size={11} />
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        ) : (
          <div className="flex flex-col items-center gap-1.5 rounded-lg bg-panel-soft px-3 py-10 text-ink-mute">
            <Library size={22} />
            <span className="text-[11.5px]">{debounced ? '没有匹配的素材' : '资产库里还没有可用素材'}</span>
          </div>
        )}

        {totalPages > 1 && (
          <div className="flex items-center justify-center gap-2">
            <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              上一页
            </Button>
            <span className="text-[11.5px] text-ink-mute">
              {page} / {totalPages}
            </span>
            <Button
              size="sm"
              variant="ghost"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              下一页
            </Button>
          </div>
        )}

        <div className="flex items-center gap-1.5 border-t border-line-soft pt-3">
          <span className="min-w-0 flex-1 truncate text-[11px] text-ink-mute">
            {chosen.length ? chosen.map((item) => item.name).join('、') : '还没有选择'}
          </span>
          <Button size="sm" variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={!chosen.length}
            onClick={() => {
              onConfirm(chosen);
              onClose();
            }}
          >
            确认选择
          </Button>
        </div>
      </div>
    </Modal>
  );
}
