import { useEffect, useMemo, useRef, useState } from 'react';
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
  /**
   * 已选项的完整对象（可选）。
   *
   * 只有 id 时，**翻页/搜索后看不到的已选项会在确认时凭空消失**：`picked` 里没有它，
   * 调用方拿到的数组就缺一项，于是「解绑了」这件事被当成用户的意图。文本引用那条链
   * 更严重——数组顺序就是 `{{Text N}}` 的编号顺序，少一项会让后面的编号整体前移。
   * 传了它就按 `value` 的顺序原样带回。
   */
  valueItems?: LibraryAssetOut[];
  onClose: () => void;
  onConfirm: (assets: LibraryAssetOut[]) => void;
}

/** 未显式传 `kinds` 时的候选类型：文本资产是给提示词占位符用的，默认不进选择器 */
const DEFAULT_KINDS: LibraryAssetKind[] = ['video', 'image', 'audio'];

/**
 * 资产库选择器：给模板库 / 生图 / 工作台挑素材用。
 *
 * 只打 `GET /library/selection`（硬过滤 `state=active`）：草稿与回收站里的东西
 * 绝不能进生成任务，所以这不是一个可以从前端关掉的筛选参数。
 *
 * 选中项按 **id → 资产** 存在本地：确认时要把名称一起带回去（调用方要显示
 * 「已选 3 项：主视觉、产品图…」），只回 id 就得再查一次。
 *
 * 类型口径：`kinds` 未传时默认只给「成片 / 图片 / 音频」，**排除 text 与 pack**。
 * 只有调用方显式把 `'text'` 放进 `kinds`（文本引用选择器）才会显示文本资产，
 * 否则图片/视频/参考图选择器不会因为后端多返回一种类型就混进文本。
 */
export function LibraryPicker({
  open,
  title = '从资产库选择',
  multiple = true,
  kinds,
  value,
  valueItems,
  onClose,
  onConfirm,
}: LibraryPickerProps) {
  const [keyword, setKeyword] = useState('');
  const [kind, setKind] = useState('');
  const [page, setPage] = useState(1);
  const [picked, setPicked] = useState<Record<string, LibraryAssetOut>>({});
  const debounced = useDebouncedValue(keyword, 250);

  /* 打开那一刻要把 `valueItems` 铺进 picked，而这个 useEffect 只认 `open`：
     直接把 valueItems 写进依赖，调用方每次渲染都新建一个数组，会把用户刚点的
     选择重置掉。用 ref 拿「最新的一份」，只在真的打开时才读。 */
  const seed = useRef(valueItems);
  useEffect(() => {
    seed.current = valueItems;
  }, [valueItems]);

  /* kinds 只有一个时把类型下发给服务端：前端过滤会让 total 与分页对不上，
     文本资产排在图片后面时可能翻好几页都看不到。 */
  const singleKind = kinds?.length === 1 ? kinds[0] : undefined;
  const query = useLibrarySelection({
    keyword: debounced || undefined,
    kind: kind || singleKind || undefined,
    page,
    pageSize: 24,
  });

  useEffect(() => {
    if (!open) return;
    setKeyword('');
    setKind('');
    setPage(1);
    // 已选项的完整对象先铺进去：翻页 / 搜索之后看不到的项也必须留在结果里，
    // 否则确认时它凭空消失，调用方只能当成「用户解绑了」——文本引用那条链
    // 还会因此把后面的 {{Text N}} 整体前移。
    setPicked(Object.fromEntries((seed.current ?? []).map((item) => [item.id, item])));
  }, [open]);

  const items = useMemo(() => {
    const list = query.data?.items ?? [];
    const allowed = kinds?.length ? kinds : DEFAULT_KINDS;
    return list.filter((item) => allowed.includes(item.kind));
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

  const chosen = useMemo(() => {
    const loaded = Object.values(picked);
    // 单选就是「换一个」，顺序没有意义
    if (!multiple) return loaded;
    const order = value ?? [];
    const known = new Set(order);
    // 已选的按 `value` 的顺序排：那是「已经定下来的编号」，不能被后点进来的挤乱
    const preserved = order
      .map((id) => picked[id])
      .filter((item): item is LibraryAssetOut => !!item);
    return [...preserved, ...loaded.filter((item) => !known.has(item.id))];
  }, [picked, value, multiple]);

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
                ...(kinds ?? DEFAULT_KINDS).map((item) => ({
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
                  {asset.kind === 'text' ? (
                    /* 文本资产没有缩略图，用摘要画文字卡，别去请求 /thumbnail 落一张破图 */
                    <div className="flex aspect-[4/3] flex-col overflow-hidden rounded-md bg-panel-soft p-1.5">
                      <span className="line-clamp-4 whitespace-pre-wrap break-words text-[10.5px] leading-[14px] text-ink-soft">
                        {asset.textExcerpt?.trim() || '（空文本）'}
                      </span>
                    </div>
                  ) : (
                    <CoverImage src={asset.coverUrl ?? undefined} seed={asset.id} className="aspect-[4/3] rounded-md" />
                  )}
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
