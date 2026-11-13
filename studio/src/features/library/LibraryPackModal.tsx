import { useEffect, useMemo, useState } from 'react';
import { Check, PackagePlus } from 'lucide-react';
import { Button, Chip, CoverImage, Modal, SearchInput, Skeleton } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { formatBytes } from '@/shared/lib/format';
import { toast } from '@/shared/lib/toast';
import { useDebouncedValue } from '@/shared/hooks/useDebouncedValue';
import { useCreatePack, useLibrarySelection } from '@/entities/library/api/library.queries';
import { KIND_LABEL, displayName } from '@/entities/library/model/types';
import { PACK_ITEM_ROLES, packItemRoleLabel } from '@/entities/library/model/packItemRoles';

export interface LibraryPackModalProps {
  open: boolean;
  /** 从批量工具栏进来时预选的成员 */
  preselected: string[];
  onClose: () => void;
  onCreated?: (packId: string) => void;
}

/**
 * 建封装：只从**库内可见**的素材里挑（后端选择器硬过滤 `state=active`），
 * 封装不能嵌套，所以候选里不出现 `kind=pack`。
 *
 * 成员上限 100 条由后端判定；这里到 100 就禁掉后面的勾选，让用户在点提交
 * 之前就知道「满了」，而不是提交后收到一个 400。
 *
 * 为什么要在这里给每个成员选**用途**：模板库把封装当参考图用时，`{{Mixed N}}`
 * 是按用途取位的（主图 / 多视图 / 细节图）。用途只存在成员行上，页面上不给入口
 * 的话，B 端选中这个封装必然撞「没有声明可用途」——而那句话能做的动作恰好就是
 * 打开这个对话框。空着不选是允许的（封装也能只用来归档），提交时如实写空串。
 */
export function LibraryPackModal({ open, preselected, onClose, onCreated }: LibraryPackModalProps) {
  const create = useCreatePack();
  const [name, setName] = useState('');
  const [keyword, setKeyword] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  /** 成员用途：键是 assetId。没选过的成员不在表里（等价于空用途）。 */
  const [roles, setRoles] = useState<Record<string, string>>({});
  const debounced = useDebouncedValue(keyword, 250);
  const query = useLibrarySelection({ keyword: debounced, pageSize: 60 });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setName('');
    setKeyword('');
    setSelected(preselected);
    setRoles({});
  }, [open, preselected]);

  const chosen = useMemo(() => new Set(selected), [selected]);
  const byId = useMemo(
    () => new Map((query.data?.items ?? []).map((asset) => [asset.id, asset])),
    [query.data],
  );

  const toggle = (id: string) => {
    setSelected((previous) =>
      previous.includes(id)
        ? previous.filter((item) => item !== id)
        : previous.length >= 100
          ? previous
          : [...previous, id],
    );
  };

  const submit = () => {
    if (!name.trim()) {
      toast('给封装起个名字', 'error');
      return;
    }
    if (!selected.length) {
      toast('至少选一个成员', 'error');
      return;
    }
    setBusy(true);
    create.mutate(
      {
        name: name.trim(),
        // 用 `items` 而不是 `assetIds`：顺序 = 勾选顺序，用途跟着成员走
        items: selected.map((assetId) => ({ assetId, role: roles[assetId] ?? '' })),
      },
      {
        onSuccess: (pack) => {
          setBusy(false);
          toast(`封装已创建，共 ${selected.length} 项`, 'success');
          onCreated?.(pack.id);
          onClose();
        },
        onError: (error) => {
          setBusy(false);
          toast(error.message, 'error');
        },
      },
    );
  };

  return (
    <Modal open={open} onClose={onClose} title="新建封装" width={720}>
      <div className="space-y-3 p-[18px]">
        <div>
          <div className="mb-1 text-[13.5px] text-ink-soft">
            封装名称 <span className="text-danger">*</span>
          </div>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="例：超市抢购·人物三视图"
            className="h-8 w-full rounded-lg border border-line bg-panel-soft px-2.5 text-[14px] text-ink outline-none focus:border-primary-line focus:bg-white focus:shadow-ring"
            aria-label="封装名称"
          />
        </div>

        <div className="flex items-center gap-2">
          <div className="w-[220px]">
            <SearchInput
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              placeholder="搜索素材…"
              aria-label="搜索封装候选素材"
            />
          </div>
          <Chip tone={selected.length >= 100 ? 'danger' : 'default'}>
            已选 {selected.length} / 100
          </Chip>
          <span className="text-[13px] text-ink-mute">只列出在库素材；封装不能作为成员</span>
        </div>

        {query.isPending ? (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(120px,1fr))] gap-2">
            {Array.from({ length: 8 }).map((_, index) => (
              <Skeleton key={index} className="h-[118px]" />
            ))}
          </div>
        ) : query.data?.items?.length ? (
          <div className="grid max-h-[300px] grid-cols-[repeat(auto-fill,minmax(120px,1fr))] gap-2 overflow-auto pr-1">
            {query.data.items.map((asset) => {
              const active = chosen.has(asset.id);
              return (
                <button
                  key={asset.id}
                  type="button"
                  onClick={() => toggle(asset.id)}
                  className={cn(
                    'relative rounded-lg border p-1.5 text-left transition',
                    active
                      ? 'border-primary bg-primary-soft/40 ring-2 ring-primary/15'
                      : 'border-line-soft bg-panel hover:border-line-strong',
                  )}
                  aria-pressed={active}
                >
                  <CoverImage src={asset.coverUrl ?? undefined} seed={asset.id} className="aspect-[4/3] rounded-md" />
                  <div className="mt-1 truncate text-[13.5px] text-ink">{displayName(asset.name)}</div>
                  <div className="truncate text-[12.5px] text-ink-mute">
                    {KIND_LABEL[asset.kind]}
                    {asset.sizeBytes != null ? ` · ${formatBytes(asset.sizeBytes)}` : ''}
                  </div>
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
          <p className="rounded-lg bg-panel-soft px-3 py-6 text-center text-[13.5px] text-ink-mute">
            没有可选的素材。先上传几张图，或者清空搜索词。
          </p>
        )}

        {selected.length > 0 && (
          <section aria-label="成员顺序与用途" className="space-y-1.5 rounded-lg bg-panel-soft p-2.5">
            <p className="text-[13px] text-ink-soft">
              成员顺序决定模板里 <code>{'{{Mixed 2}}'}</code> 起的编号；用途决定这张图在提示词里
              能决定什么。只用来归档时可以留空，模板库引用时必须是下面其中一项。
            </p>
            <ol className="space-y-1">
              {selected.map((assetId, index) => (
                <li key={assetId} className="flex items-center gap-2">
                  <span className="w-10 shrink-0 text-[12.5px] text-ink-mute">#{index + 1}</span>
                  <span className="min-w-0 flex-1 truncate text-[13.5px] text-ink">
                    {/* 预选成员可能不在当前搜索结果里：宁可用中性说法，也不把 id 摊给用户看 */}
                    {byId.get(assetId)?.name
                      ? displayName(byId.get(assetId)!.name)
                      : '已选素材（不在当前列表里）'}
                  </span>
                  <select
                    value={roles[assetId] ?? ''}
                    onChange={(event) =>
                      setRoles((previous) => ({ ...previous, [assetId]: event.target.value }))
                    }
                    aria-label={`第 ${index + 1} 项用途`}
                    className="h-7 shrink-0 rounded-lg border border-line bg-white px-1.5 text-[13px] text-ink outline-none focus:border-primary-line"
                  >
                    <option value="">不声明用途</option>
                    {PACK_ITEM_ROLES.map((role) => (
                      <option key={role} value={role}>
                        {packItemRoleLabel(role)}
                      </option>
                    ))}
                  </select>
                </li>
              ))}
            </ol>
          </section>
        )}

        <div className="flex items-center gap-1.5 border-t border-line-soft pt-3">
          <Button size="sm" variant="ghost" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            size="sm"
            variant="primary"
            className="ml-auto"
            icon={<PackagePlus size={13} />}
            loading={busy}
            onClick={submit}
          >
            创建封装
          </Button>
        </div>
      </div>
    </Modal>
  );
}
