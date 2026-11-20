import { useState } from 'react';
import { Layers } from 'lucide-react';
import { useLibraryPacks } from '@/entities/library/api/library.queries';
import type { SpecSlotOut } from '@/shared/api/schema';
import { CoverImage } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { AssetChip } from '@/entities/library/ui/AssetChip';
import { PackHoverPreview } from '@/entities/library/ui/PackHoverPreview';

/**
 * 封装容器在**模板库**这边的形态。
 *
 * 作者把几个槽位放进同一个容器（槽位上的 `group`），使用者看到的就是**一个**
 * 选包入口：上面一格一格的包，点一次，按标签把成员分到下面这几格。
 *
 * 为什么不复用 `SlotMediaPicker`：那个组件的粒度是「一个槽位」，而容器的
 * 语义是「一次选择，多格一起定」。硬塞进去会有两套计数、两个「已选」，
 * 正好是用户报的那个「点了只一次，但显示还是两个」。
 */
export function PackGroupPicker({
  members,
  values,
  onPickPack,
  onClear,
}: {
  /** 容器里的槽位（顺序即编号顺序）。 */
  members: SpecSlotOut[];
  values: Record<string, string[] | undefined>;
  onPickPack: (packId: string) => void;
  /** 清掉某一格（chip 上的 ×）。 */
  onClear: (slotKey: string) => void;
}) {
  const [hoveredPackId, setHoveredPackId] = useState<string | null>(null);
  const [pointer, setPointer] = useState<{x: number; y: number} | null>(null);

  /* 候选包按成员的标签并集取：后端返回「成员标签与它交集非空」的包，
     所以列出来的包一定能填上其中至少一格。 */
  const tagIds = Array.from(
    new Set(members.flatMap(member => member.tagIds ?? [])),
  ).sort();
  const packs = useLibraryPacks({tagIds}, tagIds.length > 0);
  const rows = packs.data ?? [];
  const filled = members.filter(member => (values[member.key] ?? []).length > 0).length;

  return (
    <section className="rounded-xl border border-[rgba(211,224,229,.7)] bg-canvas/45 p-2.5 shadow-[inset_0_1px_0_rgba(255,255,255,.9)]">
      <div className="flex items-baseline gap-1.5">
        <span className="flex items-center gap-1 text-xs font-medium text-ink">
          <Layers size={12} className="text-primary" aria-hidden />
          封装槽位
        </span>
        <span
          className={cn(
            'text-micro',
            filled === members.length ? 'text-ok' : 'text-warn-deep',
          )}
        >
          {filled}/{members.length}
        </span>
        <span className="ml-auto text-micro text-ink-mute">
          {tagIds.length ? '选一个包，这几格一起填好' : '这些槽位还没圈候选标签'}
        </span>
      </div>

      {tagIds.length > 0 && rows.length === 0 ? (
        <p className="py-3 text-center text-meta text-ink-mute">
          这批标签下还没有封装包。去资产库把要一起用的几张打成一个封装。
        </p>
      ) : (
        <div className="mt-2 grid grid-cols-4 gap-1.5">
          {rows.map(pack => (
            <button
              key={pack.id}
              type="button"
              title={`${pack.name}（封装 · ${pack.memberCount ?? 0} 张）`}
              onClick={() => onPickPack(pack.id)}
              onMouseEnter={event => {
                setHoveredPackId(pack.id);
                setPointer({x: event.clientX, y: event.clientY});
              }}
              onMouseMove={event => setPointer({x: event.clientX, y: event.clientY})}
              onMouseLeave={() => {
                setHoveredPackId(null);
                setPointer(null);
              }}
              className="group relative aspect-square overflow-hidden rounded-lg border border-line-soft transition hover:border-primary-line"
            >
              <CoverImage src={pack.coverUrl ?? undefined} seed={pack.id} className="h-full w-full" />
              <span className="absolute inset-x-0 bottom-0 flex items-center justify-center gap-0.5 bg-[rgba(12,24,30,.62)] py-px text-[9px] leading-[11px] text-white">
                <Layers size={8} />
                {pack.memberCount ?? 0} 张
              </span>
            </button>
          ))}
        </div>
      )}

      {/* 每格一行：填了就显示那张（可撤），没填写「待填」。
          这里不摆网格 —— 容器里的槽位只由包决定，给它们各自的网格等于
          把「一次选择」拆回「两次选择」。 */}
      <ul className="mt-2 flex flex-col gap-1">
        {members.map(member => {
          const picked = values[member.key] ?? [];
          return (
            <li key={member.key} className="flex items-center gap-2 text-micro">
              <span className="w-16 shrink-0 truncate text-ink-soft">
                {member.label || member.key}
              </span>
              {picked.length ? (
                <AssetChip assetId={picked[0]} onRemove={() => onClear(member.key)} />
              ) : (
                <span className="text-ink-mute">
                  {member.minCount === 0 ? '可留空' : '待填'}
                </span>
              )}
            </li>
          );
        })}
      </ul>

      <PackHoverPreview packId={hoveredPackId} pointer={pointer} />
    </section>
  );
}
