import { useMemo } from 'react';
import { Check, Filter } from 'lucide-react';
import { Button, CoverImage, EmptyState } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import type { TemplatePackOut } from '@/shared/api/schema';
import { useTemplatePackCandidates } from '@/entities/template-pack/api/templatePack.queries';

export interface TemplatePackRuntimePickerProps {
  pack: TemplatePackOut;
  /** 当前选材：{token: assetId}。 */
  picks: Record<string, string>;
  onChange: (picks: Record<string, string>) => void;
}

/**
 * 运行时选材：用户在模板库用一条封装模板时，逐槽位从合规素材里挑。
 *
 * **候选池由服务端算**（`GET /{id}/candidates`）：筛选条件（类别 + 一级标签）
 * 与可见性都有各自的判据，前端自己筛一遍必然漂移 —— 而漂移的方向总是
 * 「多给权限」。
 *
 * 提交前还有一道服务端复核（`POST /{id}/verify`），因为**前端过滤只是 UI
 * 便利，接口可以被直接调用**。模板承诺的约束必须由服务端兑现。
 */
export function TemplatePackRuntimePicker({
  pack,
  picks,
  onChange,
}: TemplatePackRuntimePickerProps) {
  const slots = (pack.slots ?? []).filter(slot => slot.slotKind === 'filtered');

  if (!slots.length) {
    return (
      <EmptyState
        title="这条模板没有需要你选的槽位"
        description="所有素材都已固定在模板里，直接使用即可。"
      />
    );
  }

  return (
    <div className="space-y-3">
      {slots.map(slot => (
        <SlotPicker
          key={slot.token}
          packId={pack.id}
          token={slot.token}
          label={slot.label || '选择素材'}
          required={slot.required ?? true}
          picked={picks[slot.token] ?? ''}
          onPick={assetId =>
            onChange({...picks, [slot.token]: assetId})
          }
        />
      ))}
    </div>
  );
}

function SlotPicker({
  packId,
  token,
  label,
  required,
  picked,
  onPick,
}: {
  packId: string;
  token: string;
  label: string;
  required: boolean;
  picked: string;
  onPick: (assetId: string) => void;
}) {
  const candidates = useTemplatePackCandidates(packId, token);
  const items = useMemo(() => candidates.data?.items ?? [], [candidates.data?.items]);

  const pickedItem = useMemo(
    () => items.find(item => item.assetId === picked),
    [items, picked],
  );

  return (
    <div className="rounded-xl border border-line bg-white p-3">
      <div className="mb-2 flex items-center gap-2">
        <code className="rounded bg-panel-soft px-1.5 py-0.5 text-[11px] text-ink-soft">
          {token}
        </code>
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-ink">
          {label}
        </span>
        {required && <span className="text-[10.5px] text-danger">必选</span>}
        <span className="flex items-center gap-1 text-[10.5px] text-ink-mute">
          <Filter size={10} />
          {candidates.data?.filterKind ?? ''}
          {candidates.data?.filterTagIds?.length
            ? ` · ${candidates.data.filterTagIds.length} 个标签`
            : ''}
        </span>
      </div>

      {candidates.isLoading ? (
        <p className="py-4 text-center text-[11px] text-ink-mute">加载中…</p>
      ) : items.length === 0 ? (
        <p className="py-4 text-center text-[11px] text-ink-mute">
          没有符合条件的素材。换一个筛选条件，或先去资产库补素材。
        </p>
      ) : (
        <ul className="grid grid-cols-4 gap-2 sm:grid-cols-6">
          {items.map(item => {
            const active = item.assetId === picked;
            return (
              <li key={item.assetId}>
                <button
                  type="button"
                  onClick={() => onPick(item.assetId)}
                  aria-pressed={active}
                  title={item.name}
                  className={cn(
                    'relative block w-full overflow-hidden rounded-lg border transition-colors',
                    active
                      ? 'border-primary ring-2 ring-primary/20'
                      : 'border-line hover:border-line-strong',
                  )}
                >
                  <CoverImage
                    src={item.coverUrl}
                    seed={item.assetId}
                    alt={item.name}
                    className="aspect-square w-full"
                  />
                  <span className="block truncate px-1 py-0.5 text-left text-[10px] text-ink-mute">
                    {item.name}
                  </span>
                  {active && (
                    <span className="absolute right-1 top-1 rounded-full bg-primary p-0.5 text-white">
                      <Check size={9} />
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {picked && !pickedItem && items.length > 0 && (
        <p className="mt-2 text-[11px] text-ink-mute">
          已选：{picked}
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => onPick('')}
            className="ml-2"
          >
            清除
          </Button>
        </p>
      )}
    </div>
  );
}
