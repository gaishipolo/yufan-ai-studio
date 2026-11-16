import { useState } from 'react';
import { ChevronDown, GripVertical, Trash2 } from 'lucide-react';
import { AssetChip } from '@/entities/library/ui/AssetChip';
import { PackChip } from '@/entities/library/ui/PackChip';
import type { SpecSlotIn } from '@/shared/api/schema';
import { Button, FormField, Select, TextInput } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { SLOT_TYPE_LABELS } from './specDraft';
import { SLOT_TYPE_ICONS } from './slotIcons';

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
  const TypeIcon = SLOT_TYPE_ICONS[slot.type] ?? SLOT_TYPE_ICONS.image;

  return (
    <div
      onDragOver={onDragOver}
      onDrop={onDrop}
      className={cn(
        // 槽位是一行，不是一张卡：一排槽位用细线分隔就够了，
        // 每行都画一圈边框会让这一栏变成「一堆小方块」——缩到 50% 看，
        // 四块一样的方块根本分不出谁是谁。
        // 固定的槽位只把左边描一道绿边（而不是整行铺绿底）：绿底会让
        // 「少数固定槽位」比「多数用户填槽位」更抢眼，扫一眼反而分不清主次。
        'relative flex flex-col px-1 py-2 transition-colors',
        dragging && 'rounded-lg bg-primary-soft/70 ring-1 ring-primary/20',
      )}
    >
      <div className="flex items-center gap-2">
        <span
          draggable
          onDragStart={onDragStart}
          onDragEnd={onDragEnd}
          className="flex shrink-0 cursor-grab items-center gap-1.5 text-meta text-ink-mute active:cursor-grabbing"
          title="拖动调整顺序（顺序即编号）"
        >
          <GripVertical size={13} />
          <span className="flex h-[18px] w-[18px] items-center justify-center rounded-full bg-primary text-micro font-semibold text-white shadow-[0_1px_3px_rgba(43,124,255,.35)]">
            {index + 1}
          </span>
        </span>

        {/* 类型标记：以前类型藏在下拉框里，要点开才知道这个槽位要图片还是成片。
            现在一眼可见，也提醒「改类型」这个动作存在（展开区里）。
            改成图标是因为文字chip（"成片"/"图片"）在 14px 的行里跟展示名挤成一团，
            图标 + 可读名称（给读屏）最省地方。 */}
        <span
          role="img"
          aria-label={SLOT_TYPE_LABELS[slot.type]}
          title={`类型：${SLOT_TYPE_LABELS[slot.type]}`}
          className="grid size-7 shrink-0 place-items-center rounded-lg bg-panel-soft text-ink-soft ring-1 ring-inset ring-line-soft"
        >
          <TypeIcon size={14} />
        </span>

        {/* 展示名直接就是标题：以前它躺在一堆下拉框中间，要先找才知道这个槽位叫什么。
            空着时显示占位提示，一眼知道还没起名。
            `min-w-0` 是关键：没有它，这个 input 会按内容宽度撑开父级
            （素材名当展示名时尤其明显），把后面的按钮全挤出可视区。
            边框平时是隐形的：它长得像一行标题而不是一个输入框，
            鼠标移上去或点进去才显出边框 —— 「看着像文本、点进去能改」。 */}
        <input
          value={slot.label ?? ''}
          placeholder={name}
          aria-label="展示名"
          onChange={event => onPatch({label: event.target.value})}
          className={cn(
            'min-w-0 flex-1 rounded-md border border-transparent bg-transparent px-1.5 py-1 text-body outline-none transition-colors',
            'hover:border-line-soft focus:border-primary-line focus:bg-panel focus:ring-2 focus:ring-primary/10',
            slot.label?.trim() ? 'text-ink' : 'placeholder:text-ink-mute',
          )}
        />

        {/* 谁定：两个选项的东西，没有下拉值得占一格 —— 直接一个分段控件。
            选中项是**实心**的：这是这一行里唯一需要被一眼看出来的状态，
            「这一格在说什么」比「这一格是什么颜色」更重要。
            「用户填」是绝大多数槽位的默认档，所以它默认就是实心的。 */}
        <div className="flex shrink-0 items-center gap-0.5 rounded-lg border border-line-soft bg-panel-soft p-0.5">
          {BINDING_TOGGLES.map(item => {
            const active = (slot.binding ?? 'user') === item.value;
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
                  'rounded-[7px] px-2 py-[3px] text-micro transition-all',
                  active
                    ? 'bg-ink font-medium text-white shadow-[0_1px_2px_rgba(21,50,61,.28)]'
                    : 'text-ink-mute hover:text-ink-soft',
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
          className="mt-2 inline-flex items-center gap-1 rounded-md bg-warn-soft px-1.5 py-0.5 text-micro text-warn-deep transition-colors hover:bg-warn/15"
        >
          缺一级标签 · 发布前必须选
        </button>
      )}

      {/* 固定槽位选好了东西：显示它是什么，而不是一串 id。
          单独一行、缩进对齐到槽位名，左边一道绿 —— 扫一眼就知道
          「这几个是作者定死的，那几个留给使用者挑」。 */}
      {fixed && (
        <div className="mt-2 ml-9 flex items-center gap-2 rounded-lg border border-line-soft border-l-[3px] border-l-ok bg-panel-soft py-1.5 pl-2.5 pr-2">
          {slot.type === 'pack' ? (
            slot.packId ? (
              <PackChip packId={slot.packId} />
            ) : (
              <span className="text-meta text-ink-mute">还没选封装</span>
            )
          ) : slot.assetId ? (
            <AssetChip assetId={slot.assetId} className="border-transparent bg-panel" />
          ) : (
            <span className="text-meta text-ink-mute">还没选素材</span>
          )}
          <Button
            onClick={onPickAsset}
            variant="secondary"
            size="sm"
            className="ml-auto shrink-0"
          >
            {slot.assetId || slot.packId ? '更换' : slot.type === 'pack' ? '选择封装' : '选择素材'}
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
              hint="使用者按它挑素材"
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
            <p className="text-meta text-ink-mute sm:col-span-2">
              文本槽位：占 {`{{Text N}}`} 的第 N 位，正文由所选文案注入
            </p>
          )}
          {slot.type === 'audio' && (
            <p className="text-meta text-ink-mute sm:col-span-2">
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
