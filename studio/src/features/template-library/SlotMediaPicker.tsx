import { useEffect, useMemo, useState } from 'react';
import { Check } from 'lucide-react';
import {
  useLibrarySelection,
  useLibraryTags,
} from '@/entities/library/api/library.queries';
import { KIND_LABEL, viewQuery } from '@/entities/library/model/types';
import type { SpecSlotOut } from '@/shared/api/schema';
import { CoverImage } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { AssetChip } from '@/entities/library/ui/AssetChip';
import { pickWithin } from './slotPick';

const PAGE_SIZE = 24;

export function SlotMediaPicker({
  slot,
  selected,
  onChange,
}: {
  slot: SpecSlotOut;
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  const kind = slot.type as 'image' | 'video';
  const label = KIND_LABEL[kind];
  const max = Math.max(1, slot.maxCount ?? 1);
  const tagId = slot.tagId ?? '';

  const tags = useLibraryTags({ ...viewQuery('all'), kind });
  const group = (tags.data ?? []).find(tag => tag.id === tagId);
  const children = useMemo(
    () => (tags.data ?? []).filter(tag => tag.parentId === tagId),
    [tags.data, tagId],
  );

  /* 用户点的那一档二级标签；null = 还没点。换槽位要重置，否则切模板后还停在
     上一个模板的标签上。 */
  const [picked, setPicked] = useState<string | null>(null);
  useEffect(() => setPicked(null), [tagId, slot.key]);

  /* 有二级标签就必须先点一下再看素材：一行全摊开太长，干脆不设「全部」。
     没有二级标签（或槽位还没设一级标签）时没有可点的，直接看一级标签自己。 */
  const needPick = children.length > 0;
  const scope = picked ?? (needPick ? '' : tagId);

  const list = useLibrarySelection({
    kind,
    tagIds: scope ? [scope] : undefined,
    pageSize: PAGE_SIZE,
    /* 只有「标签已就绪、确实有二级标签、还没点」这一种情况才不发请求；
       标签读取失败时别把整个选择器锁死，照旧给一级标签下的素材。 */
    enabled: !(tags.isSuccess && needPick && picked === null),
  });
  const items = list.data?.items ?? [];

  const toggle = (assetId: string) => {
    const next = pickWithin(selected, assetId, max);
    if (next !== selected) onChange(next);
  };

  const full = selected.length >= max;

  return (
    <div className="rounded-xl border border-line-soft bg-panel-soft p-2.5">
      <div className="flex items-baseline gap-1.5">
        <span className="min-w-0 truncate text-[12px] font-medium text-ink">
          {group ? `${group.name} · ${slot.label || slot.key}` : (slot.label || slot.key)}
        </span>
        {(slot.minCount ?? 1) > 0 && <span className="text-warn-deep">*</span>}
        <span className="ml-auto shrink-0 text-[10px] text-ink-mute">
          {label} · {max > 1 ? `已选 ${selected.length}/${max}` : '选 1 个'}
          {typeof list.data?.total === 'number' && scope && ` · 共 ${list.data.total}`}
        </span>
      </div>

      {!tagId && (
        /* 老模板（闸门加上之前发布的）没有一级标签：仍然能挑，但要说明为什么没有分组。 */
        <p className="mt-1 text-[10px] leading-relaxed text-warn-deep">
          这个槽位还没设一级标签（去「模板封装」补一个才能按标签分组），下面是全部{label}。
        </p>
      )}

      {selected.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {selected.map(assetId => (
            <AssetChip
              key={assetId}
              assetId={assetId}
              onRemove={() => onChange(selected.filter(id => id !== assetId))}
            />
          ))}
        </div>
      )}

      {children.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {/* 只有二级标签，不带「全部」：一行摊开本来就长，再放一个全量入口更乱。
              也不带计数：标签接口的数字是「列表口径」（被装进封装的成员不单列），
              而这里挑素材用的是「选择器口径」（成员也要能挑），两者对不上。
              真实条数放在右上角「共 N」——它来自这一档实际拉到的数据。 */}
          {children.map(child => (
            <ScopeChip
              key={child.id}
              active={picked === child.id}
              label={child.name}
              onClick={() => setPicked(child.id)}
            />
          ))}
        </div>
      )}

      {tags.isPending ? (
        <p className="py-4 text-center text-[11px] text-ink-mute">正在读取标签…</p>
      ) : needPick && !scope ? (
        <p className="py-4 text-center text-[11px] text-ink-mute">点一个标签，下面是它的素材。</p>
      ) : list.isLoading ? (
        <p className="py-4 text-center text-[11px] text-ink-mute">正在读取素材…</p>
      ) : items.length === 0 ? (
        <p className="py-4 text-center text-[11px] text-ink-mute">
          {tagId ? '这个标签下还没有素材，去资产库给它打上标签。' : `还没有${label}。`}
        </p>
      ) : (
        <div className="mt-2 grid max-h-[188px] grid-cols-4 gap-1.5 overflow-y-auto pr-0.5">
          {items.map(item => {
            const isPicked = selected.includes(item.id);
            const blocked = full && !isPicked && max > 1;
            return (
              <button
                key={item.id}
                type="button"
                title={item.name}
                disabled={blocked}
                onClick={() => toggle(item.id)}
                className={cn(
                  'group relative aspect-square overflow-hidden rounded-lg border transition',
                  isPicked
                    ? 'border-primary-line ring-2 ring-primary/25'
                    : 'border-line-soft hover:border-line-strong',
                  blocked && 'cursor-not-allowed opacity-40',
                )}
              >
                <CoverImage
                  src={item.coverUrl ?? undefined}
                  seed={item.id}
                  className="h-full w-full"
                />
                {isPicked && (
                  <span className="absolute right-1 top-1 grid size-4 place-items-center rounded-full bg-primary text-white">
                    <Check size={10} strokeWidth={3} />
                  </span>
                )}
                {item.kind === 'video' && (
                  <span className="absolute bottom-1 left-1 rounded bg-black/55 px-1 text-[10px] text-white">
                    视频
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** 二级标签的胶囊。点一下只换「看哪一档」，不改已选的素材。 */
function ScopeChip({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        'rounded-full border px-2 py-[2px] text-[10.5px] transition-colors',
        active
          ? 'border-primary-line bg-primary-soft font-medium text-primary-deep'
          : 'border-line bg-panel text-ink-soft hover:border-line-strong hover:text-ink',
      )}
    >
      {label}
    </button>
  );
}
