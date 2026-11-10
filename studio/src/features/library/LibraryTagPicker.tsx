import { useMemo, useState } from 'react';
import { Check, Plus } from 'lucide-react';
import { Button, Modal } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import type { LibraryAssetOut, LibraryTagStatOut } from '@/shared/api/schema';
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
  'inline-flex items-center gap-1 rounded-full border px-2 py-[3px] text-[11px] leading-4 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40';
const IDLE =
  'border-line-soft bg-panel-soft text-ink-soft hover:border-primary-line hover:bg-primary-soft/60 hover:text-primary-deep';
const ACTIVE = 'border-primary bg-primary text-white shadow-sm';

/**
 * 卡片标签行末尾那个 `+` 点开的东西。
 *
 * 为什么是弹窗而不是卡片内的浮层：卡片根节点有 `overflow-hidden`（封面要跟着
 * 圆角裁），任何绝对定位的下拉都会被裁掉半个；弹窗走 portal 到 body，没有这问题，
 * 也顺带拿到现成的 Esc / 点遮罩关闭 / 焦点归还。
 *
 * 只列**还没挂在这条素材上**的标签：已经挂了还摆出来，用户点一下等于什么都没做。
 * 提交时把现有 id 与本次选中的 id 合成完整数组，和卡片上的 × 共用同一个
 * `PATCH /assets/{id}` 的 `tagIds` 整体替换语义。
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
  const [created, setCreated] = useState<{ value: string; label: string }[]>([]);
  const [draft, setDraft] = useState('');
  const [creating, setCreating] = useState(false);

  /* 新建成功的标签本地先记一份：词表要等一次失效重拉才回流，这一帧里
     选项不能凭空消失，否则用户会以为刚建的没选上。`value` 去重兜住回流后的重复项。 */
  const options = useMemo(() => {
    const all = [
      ...tags.map((tag) => ({ value: tag.id, label: tag.name })),
      ...created,
    ];
    const seen = new Set<string>();
    return all.filter((option) => {
      if (seen.has(option.value) || currentIds.includes(option.value)) return false;
      seen.add(option.value);
      return true;
    });
  }, [tags, created, currentIds]);

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
      const id = await onCreateTag(name);
      if (!id) return;
      setCreated((previous) =>
        previous.some((option) => option.value === id)
          ? previous
          : [...previous, { value: id, label: name }],
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
      width={420}
      title={`给「${displayName(asset.name)}」添加标签`}
    >
      <div className="space-y-3 p-[18px]">
        <p className="text-[11.5px] leading-5 text-ink-mute">
          点选已有标签，或输入新名称回车创建；一次可以加多个。已有标签请在卡片上点 × 移除。
        </p>

        <div className="flex max-h-[220px] min-h-[52px] flex-wrap content-start gap-1.5 overflow-auto rounded-lg border border-line-soft bg-panel-soft/50 p-2">
          {options.length === 0 ? (
            <span className="px-1 py-2 text-[11.5px] text-ink-mute">
              词表里的标签都已添加，可以在下方新建一个
            </span>
          ) : (
            options.map((option) => {
              const active = picked.includes(option.value);
              return (
                <button
                  key={option.value}
                  type="button"
                  aria-pressed={active}
                  disabled={busy}
                  onClick={() => toggle(option.value)}
                  className={cn(PILL, active ? ACTIVE : IDLE, 'disabled:opacity-50')}
                >
                  {active && <Check size={11} strokeWidth={2.5} />}
                  <span className="max-w-[140px] truncate">{option.label}</span>
                </button>
              );
            })
          )}
        </div>

        {onCreateTag && (
          <div className="flex items-center gap-1.5">
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
              className="h-8 min-w-0 flex-1 rounded-lg border border-line bg-white px-2.5 text-xs text-ink placeholder:text-ink-mute focus:border-primary-line focus:outline-none focus:ring-2 focus:ring-primary/10 disabled:bg-panel-soft"
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
          <span className="text-[11px] text-ink-mute">
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
