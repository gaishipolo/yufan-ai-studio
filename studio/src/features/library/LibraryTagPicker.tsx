import { useMemo, useState } from 'react';
import { Check, Plus } from 'lucide-react';
import { Button, Modal } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import type { LibraryAssetOut, LibraryTagStatOut } from '@/shared/api/schema';
import {
  selectableTags,
  tagLabel,
  topLevelTags,
} from '@/entities/library/model/tagTree';
import { displayName } from '@/entities/library/model/types';
import type { CreateTagInline } from './use-create-tag-inline';

export interface LibraryTagPickerProps {
  asset: LibraryAssetOut;
  /** 整个词表（含 0 条的），来自当前入口的 `GET /library/tags` */
  tags: LibraryTagStatOut[];
  busy?: boolean;
  /** 没有 `library:create` 时不传：不摆一个必定失败的输入框 */
  onCreateTag?: CreateTagInline;
  /** 交回**完整**的 tagIds，上层一次 PATCH 写回，避免「新增」变成新接口 */
  onSubmit: (asset: LibraryAssetOut, tagIds: string[]) => void;
  onClose: () => void;
}

const PILL =
  'inline-flex items-center gap-1 rounded-full border px-2.5 py-[3px] text-[13px] leading-4 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40';
const IDLE =
  'border-line-soft bg-panel-soft text-ink-soft hover:border-primary-line hover:bg-primary-soft/60 hover:text-primary-deep';
const ACTIVE = 'border-primary bg-primary text-white shadow-sm';

interface Option {
  value: string;
  label: string;
  parentId: string;
}

/**
 * 卡片标签行末尾那个 `+` 点开的东西。
 *
 * 只列**叶子**（`childCount === 0`）：有二级的一级是分组，后端会拒掉把它
 * 直接打在素材上的请求，摆出来等于给用户一个必定失败的按钮。叶子按一级
 * 分组显示，无父的归到「未分组」。
 *
 * 新建时可以选「作为一级」或「挂在某个一级下面」，省掉「先建再移动」两步。
 */
export function LibraryTagPicker({
  asset,
  tags,
  busy,
  onCreateTag,
  onSubmit,
  onClose,
}: LibraryTagPickerProps) {
  const currentIds = useMemo(() => (asset.tags ?? []).map((tag) => tag.id), [asset.tags]);
  const [picked, setPicked] = useState<string[]>([]);
  const [created, setCreated] = useState<Option[]>([]);
  const [draft, setDraft] = useState('');
  const [newParent, setNewParent] = useState('');
  const [creating, setCreating] = useState(false);

  /* 新建成功的标签本地先记一份：词表要等一次失效重拉才回流，这一帧里
     选项不能凭空消失，否则用户会以为刚建的没选上。`value` 去重兜住回流后的重复项。 */
  const options = useMemo<Option[]>(() => {
    const all: Option[] = [
      ...selectableTags(tags).map((tag) => ({
        value: tag.id,
        label: tagLabel(tag),
        parentId: tag.parentId ?? '',
      })),
      ...created,
    ];
    const seen = new Set<string>();
    return all.filter((option) => {
      if (seen.has(option.value) || currentIds.includes(option.value)) return false;
      seen.add(option.value);
      return true;
    });
  }, [tags, created, currentIds]);

  const groups = useMemo(() => {
    const byParent = new Map<string, Option[]>();
    for (const option of options) {
      byParent.set(option.parentId, [...(byParent.get(option.parentId) ?? []), option]);
    }
    const names = new Map(tags.map((tag) => [tag.id, tag.name]));
    return [...byParent.entries()]
      .sort(([a], [b]) => (a ? (b ? 0 : -1) : b ? 1 : 0))
      .map(([parentId, items]) => ({
        key: parentId || '_none',
        title: parentId ? names.get(parentId) ?? '二级' : '未分组',
        items,
      }));
  }, [options, tags]);

  const tops = useMemo(() => topLevelTags(tags), [tags]);

  function toggle(id: string) {
    setPicked((previous) =>
      previous.includes(id) ? previous.filter((item) => item !== id) : [...previous, id],
    );
  }

  async function submitCreate() {
    const name = draft.trim();
    if (!onCreateTag || !name || creating) return;
    setCreating(true);
    try {
      const id = await onCreateTag(name, newParent || undefined);
      if (!id) return;
      const parentName = tops.find((tag) => tag.id === newParent)?.name;
      const option: Option = {
        value: id,
        label: parentName ? `${parentName} / ${name}` : name,
        parentId: newParent,
      };
      setCreated((previous) =>
        previous.some((item) => item.value === id) ? previous : [...previous, option],
      );
      setPicked((previous) => (previous.includes(id) ? previous : [...previous, id]));
      setDraft('');
    } finally {
      setCreating(false);
    }
  }

  function submit() {
    if (!picked.length || busy) return;
    onSubmit(asset, [...currentIds, ...picked]);
  }

  return (
    <Modal
      open
      onClose={onClose}
      width={460}
      title={`给「${displayName(asset.name)}」添加标签`}
    >
      <div className="space-y-3 p-[18px]">
        <p className="text-[13.5px] leading-5 text-ink-mute">
          按一级分组列出可打标的标签；一级下还有二级时只作分组，不能直接打在素材上。已有标签请在卡片上点 × 移除。
        </p>

        <div className="flex max-h-[240px] min-h-[52px] flex-col gap-2 overflow-auto rounded-lg border border-line-soft bg-panel-soft/50 p-2">
          {groups.length === 0 ? (
            <span className="px-1 py-2 text-[13.5px] text-ink-mute">
              词表里的标签都已添加，可以在下方新建一个
            </span>
          ) : (
            groups.map((group) => (
              <div key={group.key}>
                <div className="mb-1 px-0.5 text-[12.5px] font-medium text-ink-mute">
                  {group.title}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {group.items.map((option) => {
                    const isActive = picked.includes(option.value);
                    return (
                      <button
                        key={option.value}
                        type="button"
                        aria-pressed={isActive}
                        disabled={busy}
                        onClick={() => toggle(option.value)}
                        className={cn(PILL, isActive ? ACTIVE : IDLE, 'disabled:opacity-50')}
                      >
                        {isActive && <Check size={11} strokeWidth={2.5} />}
                        <span className="max-w-[160px] truncate">{option.label}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            ))
          )}
        </div>

        {onCreateTag && (
          <div className="flex items-center gap-1.5">
            <select
              value={newParent}
              aria-label="新标签所属一级"
              disabled={creating || busy}
              onChange={(event) => setNewParent(event.target.value)}
              className="h-8 w-[112px] shrink-0 rounded-lg border border-line bg-white px-1.5 text-[13px] text-ink focus:border-primary-line focus:outline-none disabled:bg-panel-soft"
            >
              <option value="">作为一级</option>
              {tops.map((tag) => (
                <option key={tag.id} value={tag.id}>
                  {tag.name} 下
                </option>
              ))}
            </select>
            <input
              type="text"
              value={draft}
              maxLength={24}
              disabled={creating || busy}
              placeholder="新建标签"
              aria-label="新建标签名称"
              autoComplete="off"
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key !== 'Enter') return;
                event.preventDefault();
                void submitCreate();
              }}
              className="h-8 min-w-0 flex-1 rounded-lg border border-line bg-white px-2.5 text-[14px] text-ink placeholder:text-ink-mute focus:border-primary-line focus:outline-none focus:ring-2 focus:ring-primary/10 disabled:bg-panel-soft"
            />
            <Button
              size="sm"
              variant="secondary"
              disabled={!draft.trim() || creating || busy}
              icon={<Plus size={12} />}
              onClick={() => void submitCreate()}
            >
              {creating ? '新建中' : '新建'}
            </Button>
          </div>
        )}

        <div className="flex items-center justify-between gap-2 border-t border-line-soft pt-3">
          <span className="text-[13px] text-ink-mute">
            {picked.length ? `已选 ${picked.length} 个` : '还没选标签'}
          </span>
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" onClick={onClose}>
              取消
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={!picked.length || busy}
              onClick={submit}
            >
              {busy ? '添加中…' : '添加'}
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
