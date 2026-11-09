import { useEffect, useMemo, useRef, useState } from 'react';
import { Check, Inbox } from 'lucide-react';
import { Button, Chip } from '@/shared/ui';
import { ErrorState } from '@/shared/ui/ErrorState';
import { Skeleton } from '@/shared/ui/EmptyState';
import { useCan } from '@/shared/access/use-access';
import { toast } from '@/shared/lib/toast';
import { cn } from '@/shared/lib/cn';
import {
  useAdoptCandidate,
  useAdoptCandidates,
  useCandidates,
  useDiscardCandidate,
} from '@/entities/asset/api/candidate.queries';
import type { CandidateGroup } from '@/shared/api/schema';

/**
 * 候选确认区：**待办，不是分类**。
 *
 * 三条与"分类"不同的地方，都在这一段里体现：
 *
 * 1. **为 0 时整段消失**。未分类收件箱常驻是因为它是一个"位置"，空着也要能点进去；
 *    而空待办只是噪音——生成一次就会出现的东西，不需要一个常驻的空框提示它存在。
 * 2. **按 `recipe_id` 分组**。同一批 8 张必须挨着，否则"刚才那 4 张里选 2 张"
 *    在界面上无法完成：候选散落在时间线上时，用户只能凭缩略图猜哪张是哪一批。
 * 3. **不进 `GET /assets`**。它们只在横幅里出现，所以采纳之后列表会多出这几张
 *    ——这正是 R7 风险（"生成后列表没变化，用户以为东西丢了"）的处置：
 *    横幅 + 计数把东西放在用户面前，而不是塞进某个默认折叠的入口。
 *
 * ## 键盘
 *
 * `空格` 切换、`A` 采纳、`D` 弃用。**没有选中项时按 A/D 作用于当前高亮项**，
 * 这让"看一张、按一下"变成两次击键；先选再批量的路径也仍然成立。
 * 快捷键挂在整个区域上（一个 `tabIndex=0` 的 listbox），不是全局监听：
 * 页面别处的输入框不应该被 `A` 打断。
 */
export function CandidateBanner() {
  const canUpdate = useCan('asset:update');
  const query = useCandidates({enabled: canUpdate});
  const adopt = useAdoptCandidate();
  const adoptMany = useAdoptCandidates();
  const discard = useDiscardCandidate();
  const [selected, setSelected] = useState<string[]>([]);
  const [activeId, setActiveId] = useState<string>('');
  const containerRef = useRef<HTMLDivElement>(null);

  const groups = useMemo(() => query.data?.groups ?? [], [query.data]);
  const flat = useMemo(
    () => groups.flatMap((group) => group.items ?? []),
    [groups],
  );

  /* 采纳/弃用后这一批会消失，选中集要跟着瘦身：留着已消失的 id，
     下一次批量采纳会带着它们发请求，而那条已经不存在了。 */
  useEffect(() => {
    const alive = new Set(flat.map((item) => item.id));
    setSelected((current) => current.filter((id) => alive.has(id)));
    setActiveId((current) => (alive.has(current) ? current : (flat[0]?.id ?? '')));
  }, [flat]);

  if (!canUpdate) return null;
  if (query.isPending && !query.data) {
    return (
      <section role="status" aria-label="正在读取待确认的生成结果" className="rounded-card border border-line bg-panel p-3">
        <Skeleton className="h-16" />
      </section>
    );
  }
  if (query.error) {
    return (
      <ErrorState
        error={query.error}
        title="待确认的生成结果读取失败"
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  // 空待办只是噪音：整段消失，而不是留一个"暂无待确认"的空框
  if (!flat.length) return null;

  const busy = adopt.isPending || adoptMany.isPending || discard.isPending;
  const targets = selected.length ? selected : activeId ? [activeId] : [];

  const move = (delta: number) => {
    if (!flat.length) return;
    const index = flat.findIndex((item) => item.id === activeId);
    const next = Math.min(flat.length - 1, Math.max(0, (index < 0 ? 0 : index) + delta));
    setActiveId(flat[next].id);
  };

  const toggle = (id: string) =>
    setSelected((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );

  const runAdopt = async (ids: string[]) => {
    if (!ids.length) return;
    try {
      if (ids.length === 1) await adopt.mutateAsync({assetId: ids[0]});
      else await adoptMany.mutateAsync({assetIds: ids});
      setSelected([]);
      toast(ids.length === 1 ? '已采纳，可在素材库里找到它' : `已采纳 ${ids.length} 张`, 'success');
    } catch (error) {
      // 批量采纳是全成或全不成：失败就是"一张都没动"，提示必须这么说
      toast((error as Error).message, 'error');
    }
  };

  const runDiscard = async (ids: string[]) => {
    if (!ids.length) return;
    try {
      // 弃用没有批量接口：逐条发。一条失败不影响其余，提示里说清失败几条
      const results = await Promise.allSettled(ids.map((id) => discard.mutateAsync(id)));
      const failed = results.filter((item) => item.status === 'rejected').length;
      setSelected([]);
      if (failed) toast(`${ids.length - failed} 张已弃用，${failed} 张失败`, 'error');
      else toast(ids.length === 1 ? '已弃用' : `已弃用 ${ids.length} 张`, 'success');
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  };

  return (
    <section
      aria-label="待确认的生成结果"
      className="rounded-card border border-primary-line bg-primary-soft/40 p-3"
    >
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Inbox size={15} className="text-primary-deep" />
        <h2 className="text-[12.5px] font-semibold text-ink">待确认 {flat.length} 张</h2>
        <span className="text-[11px] text-ink-soft">
          生成结果不会自动进库：确认后进入「我的素材」，过期未确认的按宽限期清理（只清文件，配方留着）。
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <Button size="sm" disabled={!targets.length || busy} onClick={() => void runAdopt(targets)}>
            {/* 带数量而不是只写"采纳"：选了 1 张时按钮看起来要和"什么都没选"不同，
                否则用户点完不知道这一次会动几张 */}
            采纳{targets.length ? ` ${targets.length} 张` : ''}
          </Button>
          <Button
            size="sm"
            disabled={!targets.length || busy}
            onClick={() => void runDiscard(targets)}
          >
            弃用
          </Button>
          {selected.length > 0 && (
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setSelected([])}>
              取消选择
            </Button>
          )}
        </div>
      </div>

      <div
        ref={containerRef}
        role="listbox"
        aria-multiselectable="true"
        aria-activedescendant={activeId ? `candidate-${activeId}` : undefined}
        aria-label="待确认的生成结果，方向键移动，空格选择，A 采纳，D 弃用"
        tabIndex={0}
        onKeyDown={(event) => {
          const key = event.key;
          if (key === 'ArrowRight' || key === 'ArrowDown') {
            event.preventDefault();
            move(1);
            return;
          }
          if (key === 'ArrowLeft' || key === 'ArrowUp') {
            event.preventDefault();
            move(-1);
            return;
          }
          if (key === ' ' || key === 'Spacebar') {
            // 不拦的话页面会跟着滚一屏，而用户以为自己在"打勾"
            event.preventDefault();
            if (activeId) toggle(activeId);
            return;
          }
          if (key === 'a' || key === 'A') {
            event.preventDefault();
            void runAdopt(targets);
            return;
          }
          if (key === 'd' || key === 'D') {
            event.preventDefault();
            void runDiscard(targets);
          }
        }}
        className="max-h-[42vh] space-y-2 overflow-auto rounded-lg outline-none focus-visible:shadow-ring"
      >
        {groups.map((group) => (
          <CandidateRow
            key={group.recipeId || group.createdAt || 'group'}
            group={group}
            activeId={activeId}
            selected={selected}
            busy={busy}
            onActivate={setActiveId}
            onToggle={toggle}
            onAdoptGroup={(ids) => void runAdopt(ids)}
          />
        ))}
      </div>
      <p className="mt-2 text-[11px] text-ink-mute">
        快捷键：空格选择 · A 采纳 · D 弃用（未选择时作用于高亮的那一张）
      </p>
    </section>
  );
}

function CandidateRow({
  group,
  activeId,
  selected,
  busy,
  onActivate,
  onToggle,
  onAdoptGroup,
}: {
  group: CandidateGroup;
  activeId: string;
  selected: string[];
  busy: boolean;
  onActivate: (id: string) => void;
  onToggle: (id: string) => void;
  onAdoptGroup: (ids: string[]) => void;
}) {
  const items = group.items ?? [];
  const ids = items.map((item) => item.id);
  return (
    <div className="rounded-lg border border-line-soft bg-panel p-2">
      <div className="mb-1.5 flex flex-wrap items-center gap-2 text-[11px]">
        <span className="font-semibold text-ink">{group.summary || `${items.length} 张`}</span>
        {group.promptSummary && (
          <span className="min-w-0 flex-1 truncate text-ink-mute" title={group.promptSummary}>
            {group.promptSummary}
          </span>
        )}
        <Chip tone={daysLeft(group.expiresAt) <= 1 ? 'warn' : 'default'}>
          {expireText(group.expiresAt)}
        </Chip>
        <Button
          size="sm"
          variant="secondary"
          disabled={busy || !ids.length}
          onClick={() => onAdoptGroup(ids)}
        >
          整批采纳
        </Button>
      </div>
      <div className="flex gap-2 overflow-x-auto pb-1">
        {items.map((asset) => {
          const isSelected = selected.includes(asset.id);
          const isActive = activeId === asset.id;
          return (
            <div
              key={asset.id}
              id={`candidate-${asset.id}`}
              role="option"
              aria-selected={isSelected}
              tabIndex={-1}
              onClick={() => {
                onActivate(asset.id);
                onToggle(asset.id);
              }}
              onKeyDown={(event) => {
                // 键盘主路径在容器上（空格/A/D），这里只是让这一项自己也能响应
                // 回车——把它做成可聚焦项会多出 6 个 tab 停靠点，反而更难用
                if (event.key === 'Enter') {
                  event.preventDefault();
                  onActivate(asset.id);
                  onToggle(asset.id);
                }
              }}
              onMouseEnter={() => onActivate(asset.id)}
              className={cn(
                'relative w-[104px] shrink-0 cursor-pointer overflow-hidden rounded-lg border-2 bg-panel-soft transition',
                isSelected
                  ? 'border-primary shadow-ring'
                  : isActive
                    ? 'border-primary-line'
                    : 'border-transparent hover:border-line-strong',
              )}
            >
              <img
                src={asset.previewUrl ?? asset.url}
                alt={asset.name}
                loading="lazy"
                className="h-[104px] w-full object-cover"
              />
              {isSelected && (
                <span className="absolute right-1 top-1 grid h-4 w-4 place-items-center rounded-full bg-primary text-white">
                  <Check size={11} />
                </span>
              )}
              <p className="truncate px-1 py-0.5 text-[10.5px] text-ink-soft" title={asset.name}>
                {asset.name}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** 距过期还有几天（向上取整）。已过期给 0。 */
function daysLeft(iso: string | null | undefined): number {
  if (!iso) return Number.POSITIVE_INFINITY;
  const diff = new Date(iso).getTime() - Date.now();
  if (Number.isNaN(diff)) return Number.POSITIVE_INFINITY;
  return Math.max(0, Math.ceil(diff / 86_400_000));
}

function expireText(iso: string | null | undefined): string {
  const days = daysLeft(iso);
  if (!Number.isFinite(days)) return '无期限';
  if (days <= 0) return '即将过期';
  return `${days} 天后过期`;
}
