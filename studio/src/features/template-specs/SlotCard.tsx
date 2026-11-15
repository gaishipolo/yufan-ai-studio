import { useState } from 'react';
import { ChevronDown, GripVertical, Trash2 } from 'lucide-react';
import { AssetChip } from '@/entities/library/ui/AssetChip';
import { PackChip } from '@/entities/library/ui/PackChip';
import type { SpecSlotIn } from '@/shared/api/schema';
import { Button, FormField, Select, TextInput } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { SLOT_TYPE_LABELS } from './specDraft';

/**
 * 一个槽位卡片。
 *
 * **默认只露出高频项**（展示名、谁定、标签），低频项（类型、数量范围、用途说明）
 * 收进「更多设置」。以前这些平铺在一张卡上：7 个控件、高约 160px，三个槽位就
 * 1415px，滑到第三个槽位时第一个早滚出屏幕 —— 但「类型」和「最少/最多」十次里
 * 改不到一次。
 *
 * 折叠不是隐藏功能：所有字段都还在，只是收起来了。
 */
export function SlotCard({
  slot,
  index,
  dragging,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDrop,
  onPatch,
  onDelete,
  onPickAsset,
  tagOptions,
  typeOptions,
}: {
  slot: SpecSlotIn;
  index: number;
  dragging: boolean;
  onDragStart: () => void;
  onDragEnd: () => void;
  onDragOver: (event: React.DragEvent) => void;
  onDrop: () => void;
  onPatch: (patch: Partial<SpecSlotIn>) => void;
  onDelete: () => void;
  onPickAsset: () => void;
  tagOptions: {value: string; label: string}[];
  typeOptions: {value: string; label: string}[];
}) {
  const [open, setOpen] = useState(false);
  const fixed = (slot.binding ?? 'user') === 'fixed';
  // 一级标签只对「让用户挑的图片/视频」有意义：模板库按「标签 · 槽位名」分组
  // 给候选素材，文本/音频/封装走各自的选择器，固定槽位作者已经选死。
  const needsTag = slot.type === 'image' || slot.type === 'video';
  const showCount =
    slot.type !== 'pack' && (slot.binding ?? 'user') === 'user' && slot.type !== 'text';
  const name = slot.label?.trim() || `${SLOT_TYPE_LABELS[slot.type]} ${index + 1}`;

  return (
    <div
      onDragOver={onDragOver}
      onDrop={onDrop}
      className={cn(
        // 槽位是「托盘上的一张白片」：比托盘亮一档 + 一线投影，所以有前后层次。
        // 固定的槽位只把左边描一道绿边（而不是整卡铺绿底）：绿底会让"少数固定槽位"
        // 比"多数用户填槽位"更抢眼，扫一眼反而分不清主次。
        'rounded-xl border border-line-soft bg-panel p-3 transition-all',
        'shadow-[0_1px_2px_rgba(16,42,55,.05)] hover:border-line hover:shadow-card',
        fixed && 'border-l-[3px] border-l-ok',
        dragging && 'border-primary-line bg-primary-soft/60 shadow-card ring-1 ring-primary/15',
      )}
    >
      <div className="flex items-center gap-2">
        <span
          draggable
          onDragStart={onDragStart}
          onDragEnd={onDragEnd}
          className="flex shrink-0 cursor-grab items-center gap-1.5 text-[11px] text-ink-mute active:cursor-grabbing"
          title="拖动调整顺序（顺序即编号）"
        >
          <GripVertical size={13} />
          <span className="flex h-[18px] w-[18px] items-center justify-center rounded-full bg-primary text-[10px] font-semibold text-white shadow-[0_1px_3px_rgba(43,124,255,.35)]">
            {index + 1}
          </span>
        </span>

        {/* 类型标记：以前类型藏在下拉框里，要点开才知道这个槽位要图片还是成片。
            现在一眼可见，也提醒「改类型」这个动作存在（展开区里）。 */}
        <span className="shrink-0 rounded-md bg-panel-soft px-1.5 py-[2px] text-[10.5px] text-ink-mute ring-1 ring-inset ring-line-soft">
          {SLOT_TYPE_LABELS[slot.type]}
        </span>

        {/* 展示名直接就是标题：以前它躺在一堆下拉框中间，要先找才知道这个槽位叫什么。
            空着时显示占位提示，一眼知道还没起名。
            `min-w-0` 是关键：没有它，这个 input 会按内容宽度撑开父级
            （素材名当展示名时尤其明显），把后面的按钮全挤出可视区。 */}
        <input
          value={slot.label ?? ''}
          placeholder={name}
          aria-label="展示名"
          onChange={event => onPatch({label: event.target.value})}
          className={cn(
            'min-w-0 flex-1 rounded-md border bg-panel px-2 py-1 text-[12.5px] outline-none transition-colors',
            'placeholder:text-ink-mute focus:border-primary-line focus:ring-2 focus:ring-primary/10',
            slot.label?.trim() ? 'border-line-soft text-ink' : 'border-dashed border-line-strong',
          )}
        />

        {/* 谁定：两个选项的东西，没有下拉值得占一格 —— 直接两个小按钮，
            状态一目了然，也省掉「点开才知道当前是哪档」。
            「用户填」是绝大多数槽位的默认档，所以没选中时只留一个极淡的字，
            不跟标题抢注意力。 */}
        <div className="flex shrink-0 items-center gap-0.5 rounded-lg border border-line-soft bg-panel p-0.5">
          {BINDING_TOGGLES.map(item => {
            const active = (slot.binding ?? 'user') === item.value;
            const isDefault = item.value === 'user';
            return (
              <button
                key={item.value}
                type="button"
                aria-pressed={active}
                title={item.hint}
                onClick={() =>
                  onPatch({
                    binding: item.value,
                    // 固定要素材、用户填要清掉素材，两边互斥。
                    ...(item.value === 'fixed' ? {} : {assetId: null, packId: null}),
                  })
                }
                className={cn(
                  'rounded-[7px] px-1.5 py-[3px] text-[10.5px] transition-all',
                  active
                    ? isDefault
                      ? 'font-medium text-ink-soft'
                      : 'font-medium text-ok-deep'
                    : 'text-ink-mute/70 hover:text-ink-soft',
                )}
              >
                {item.label}
              </button>
            );
          })}
        </div>

        <button
          type="button"
          onClick={() => setOpen(value => !value)}
          aria-expanded={open}
          aria-label={open ? '收起更多设置' : '展开更多设置'}
          title="更多设置"
          className={cn(
            'grid size-7 shrink-0 place-items-center rounded-md text-ink-mute transition-colors hover:bg-panel-soft hover:text-ink-soft',
            open && 'bg-panel-soft text-ink-soft',
          )}
        >
          <ChevronDown size={14} className={cn('transition-transform', open && 'rotate-180')} />
        </button>

        <button
          type="button"
          className="shrink-0 rounded-md p-1 text-ink-mute transition-colors hover:bg-danger-soft hover:text-danger"
          title="删除槽位"
          onClick={onDelete}
        >
          <Trash2 size={13} />
        </button>
      </div>

      {/* 一级标签是发布闸门（用户填的图片/视频必须有它）。
          设了就安静地待在展开区；没设就在主行给一枚醒目的提示 ——
          缺它会在点发布时被 400 打回，而那句话不指名是哪个槽位。 */}
      {needsTag && !fixed && !slot.tagId && (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="mt-2 inline-flex items-center gap-1 rounded-md bg-warn-soft px-1.5 py-0.5 text-[10.5px] text-warn-deep transition-colors hover:bg-warn/15"
        >
          缺一级标签 · 发布前必须选
        </button>
      )}

      {/* 固定槽位选好了东西：显示它是什么，而不是一串 id。 */}
      {fixed && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {slot.type === 'pack' ? (
            slot.packId ? (
              <PackChip packId={slot.packId} />
            ) : (
              <span className="text-[11px] text-ink-mute">还没选封装</span>
            )
          ) : slot.assetId ? (
            <AssetChip assetId={slot.assetId} />
          ) : (
            <span className="text-[11px] text-ink-mute">还没选素材</span>
          )}
          <Button onClick={onPickAsset} variant="secondary" size="sm">
            {slot.type === 'pack' ? '选择封装' : '选择素材'}
          </Button>
        </div>
      )}

      {/* 更多设置：低频项收在这里。默认收起，主行只剩真正天天改的东西。 */}
      {open && (
        <div className="mt-3 grid gap-2 border-t border-line-soft pt-3 sm:grid-cols-2">
          {/* 固定槽位没有「一级标签/最少/最多」，只剩「类型」一个字段 ——
              不让它占满整行，右边留一块空白反而像漏了东西。 */}
          <FormField label="类型" htmlFor="slot-type" className={fixed ? 'sm:col-span-2' : undefined}>
            <Select
              id="slot-type"
              block
              value={slot.type}
              options={typeOptions}
              onChange={event => onPatch({type: event.target.value as SpecSlotIn['type']})}
            />
          </FormField>
          {needsTag && !fixed && (
            <FormField
              label="一级标签"
              htmlFor="slot-tag"
              hint="模板库按这个标签给使用者挑素材"
            >
              <Select
                id="slot-tag"
                block
                value={slot.tagId ?? TAG_ANY}
                options={tagOptions}
                onChange={event => onPatch({tagId: event.target.value})}
                className={slot.tagId ? undefined : 'border-warn'}
              />
            </FormField>
          )}
          {showCount && (
            <>
              <FormField label="最少" htmlFor="slot-min">
                <TextInput
                  id="slot-min"
                  value={String(slot.minCount ?? 1)}
                  onChange={event => onPatch({minCount: Number(event.target.value || 0)})}
                  className="text-center"
                />
              </FormField>
              <FormField label="最多" htmlFor="slot-max">
                <TextInput
                  id="slot-max"
                  value={String(slot.maxCount ?? 1)}
                  onChange={event => onPatch({maxCount: Number(event.target.value || 1)})}
                  className="text-center"
                />
              </FormField>
            </>
          )}
          <FormField
            label="进提示词的用途说明"
            htmlFor="slot-usage"
            className="sm:col-span-2"
          >
            <TextInput
              id="slot-usage"
              value={slot.usage ?? ''}
              placeholder="例如：确定同款同色外观；只补纹理与扣件"
              onChange={event => onPatch({usage: event.target.value})}
            />
          </FormField>
          {slot.type === 'text' && (
            <p className="text-[11px] text-ink-mute sm:col-span-2">
              文本槽位：占 {`{{Text N}}`} 的第 N 位，正文由所选文案注入
            </p>
          )}
          {slot.type === 'audio' && (
            <p className="text-[11px] text-ink-mute sm:col-span-2">
              附加输入：不占编号，提示词里不引用它
            </p>
          )}
        </div>
      )}
    </div>
  );
}

const BINDING_TOGGLES: {value: SpecSlotIn['binding']; label: string; hint: string}[] = [
  {value: 'user', label: '用户填', hint: '使用者填表时挑素材'},
  {value: 'fixed', label: '已固定', hint: '封装时就定好素材'},
];

/** 一级标签的「不限」哨兵：空串表示候选集不收窄。 */
const TAG_ANY = '';
