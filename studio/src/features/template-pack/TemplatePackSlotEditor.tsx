import { useMemo } from 'react';
import { Filter, Link2, Plus, Trash2 } from 'lucide-react';
import { Button, MultiSelect, Select } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import {
  tokenOf,
  type TemplatePackSlotDraft,
} from '@/entities/template-pack/model/templatePackSlots';

/** 可筛选的素材类别。与后端 `FILTERABLE_KINDS` 同一份口径（不含 text）。 */
const FILTER_KINDS = [
  {value: 'image', label: '图片'},
  {value: 'video', label: '视频'},
  {value: 'audio', label: '音频'},
];

export interface TemplatePackSlotEditorProps {
  slots: TemplatePackSlotDraft[];
  errors: Record<string, string>;
  /** 一级标签（+ 它们的二级），用来选筛选条件。 */
  tags: LibraryTagStatOut[];
  onChange: (slots: TemplatePackSlotDraft[]) => void;
  /** 点「选素材」时回调；由页面打开资产库选择器。 */
  onPickAsset: (slot: TemplatePackSlotDraft) => void;
}

/**
 * 槽位列表编辑器。
 *
 * **每个槽位二选一**（钉死素材 / 声明筛选规则）—— 这是用户拍板的口径。
 * 两个形态的字段互斥，切换形态时把另一个形态的字段清掉：
 * 留着「筛选条件」的同时又钉了素材，界面上看不出这份模板到底是哪一种约束。
 */
export function TemplatePackSlotEditor({
  slots,
  errors,
  tags,
  onChange,
  onPickAsset,
}: TemplatePackSlotEditorProps) {
  /* 筛选条件只能选一级标签：选中一级会展开成「自身 + 直属二级」
     （与资产库筛选口径一致），所以下拉里只给一级。 */
  const topTagOptions = useMemo(
    () =>
      tags
        .filter(tag => !tag.parentId)
        .map(tag => ({value: tag.id, label: tag.name})),
    [tags],
  );

  const update = (index: number, patch: Partial<TemplatePackSlotDraft>) => {
    onChange(
      slots.map((slot, i) => (i === index ? {...slot, ...patch} : slot)),
    );
  };

  const addSlot = () => {
    const position = slots.length + 1;
    onChange([
      ...slots,
      {
        token: tokenOf(position),
        slotKind: 'filtered',
        label: '',
        assetId: '',
        mediaType: 'image',
        filterKind: 'image',
        filterTagIds: [],
        required: true,
      },
    ]);
  };

  /* 删槽位要重排后面的编号 —— 编号就是 `{{Mixed N}}`，
     留个空洞会让提示词里的占位符指向谁变得不确定。 */
  const removeSlot = (index: number) => {
    const next = slots
      .filter((_, i) => i !== index)
      .map((slot, i) => ({...slot, token: tokenOf(i + 1)}));
    onChange(next);
  };

  const switchKind = (index: number, kind: 'fixed' | 'filtered') => {
    if (kind === 'fixed') {
      update(index, {slotKind: 'fixed', filterKind: '', filterTagIds: []});
      return;
    }
    update(index, {
      slotKind: 'filtered',
      assetId: '',
      filterKind: 'image',
      filterTagIds: [],
    });
  };

  return (
    <div className="space-y-2.5">
      {slots.map((slot, index) => {
        const isSource = index === 0;
        const error = errors[slot.token];
        return (
          <div
            key={slot.token}
            className={cn(
              'rounded-xl border bg-white p-3',
              error ? 'border-danger/40' : 'border-line',
            )}
          >
            <div className="flex flex-wrap items-center gap-2">
              <code className="rounded bg-panel-soft px-1.5 py-0.5 text-[11px] text-ink-soft">
                {slot.token}
              </code>
              <input
                type="text"
                value={slot.label}
                placeholder={isSource ? '参考视频' : '槽位名称，如「产品主图」'}
                maxLength={64}
                onChange={event => update(index, {label: event.target.value})}
                className="h-7 min-w-0 flex-1 rounded-lg border border-line bg-white px-2 text-xs text-ink placeholder:text-ink-mute focus:border-primary-line focus:outline-none focus:ring-2 focus:ring-primary/10"
              />

              {/* 形态二选一。第 1 槽锁死成「固定素材」：参考视频是模板的一部分，
                  不是用户能换的输入。 */}
              <div className="flex shrink-0 items-center gap-1 rounded-lg bg-panel-soft p-0.5">
                <button
                  type="button"
                  disabled={isSource}
                  onClick={() => switchKind(index, 'fixed')}
                  className={cn(
                    'flex h-6 items-center gap-1 rounded-md px-2 text-[11px] transition-colors',
                    slot.slotKind === 'fixed'
                      ? 'bg-white text-ink shadow-sm'
                      : 'text-ink-mute hover:text-ink-soft',
                    isSource && 'cursor-not-allowed opacity-70',
                  )}
                  title={isSource ? '第一个槽位固定是参考视频' : '钉死一条素材'}
                >
                  <Link2 size={11} />
                  固定素材
                </button>
                <button
                  type="button"
                  disabled={isSource}
                  onClick={() => switchKind(index, 'filtered')}
                  className={cn(
                    'flex h-6 items-center gap-1 rounded-md px-2 text-[11px] transition-colors',
                    slot.slotKind === 'filtered'
                      ? 'bg-white text-ink shadow-sm'
                      : 'text-ink-mute hover:text-ink-soft',
                    isSource && 'cursor-not-allowed opacity-70',
                  )}
                  title="声明准入规则：运行时用户只能在这条规则内选"
                >
                  <Filter size={11} />
                  筛选条件
                </button>
              </div>

              {!isSource && (
                <button
                  type="button"
                  onClick={() => removeSlot(index)}
                  aria-label={`删除 ${slot.token}`}
                  className="shrink-0 rounded-lg p-1.5 text-ink-mute transition-colors hover:bg-danger/10 hover:text-danger"
                >
                  <Trash2 size={13} />
                </button>
              )}
            </div>

            {slot.slotKind === 'fixed' ? (
              <div className="mt-2.5 flex items-center gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  onClick={() => onPickAsset(slot)}
                >
                  {slot.assetId ? '更换素材' : '选择素材'}
                </Button>
                <span className="min-w-0 flex-1 truncate text-[11px] text-ink-mute">
                  {slot.assetId ? slot.assetId : '还没选素材'}
                </span>
              </div>
            ) : (
              <div className="mt-2.5 flex flex-wrap items-center gap-2">
                <span className="text-[11px] text-ink-mute">只允许选择</span>
                <Select
                  options={FILTER_KINDS}
                  value={slot.filterKind}
                  onChange={event => update(index, {filterKind: event.target.value})}
                />
                <MultiSelect
                  options={topTagOptions}
                  value={slot.filterTagIds}
                  onChange={values => update(index, {filterTagIds: values})}
                  placeholder="不限标签"
                  emptyText="暂无一级标签"
                  ariaLabel="筛选标签"
                />
                <span className="text-[11px] text-ink-mute">
                  {slot.filterTagIds.length
                    ? '挂了这些标签的素材（选中一级会连它的二级一起算）'
                    : '该类别下的全部素材'}
                </span>
              </div>
            )}

            {error && <p className="mt-2 text-[11px] text-danger">{error}</p>}
          </div>
        );
      })}

      <Button type="button" size="sm" variant="secondary" onClick={addSlot}>
        <Plus size={13} />
        添加槽位
      </Button>

      {slots.length > 11 && (
        <p className="text-[11px] text-danger">槽位太多（参考图上限 10 张）</p>
      )}
    </div>
  );
}
