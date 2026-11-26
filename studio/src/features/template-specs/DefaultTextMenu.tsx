import type {SpecSlotIn} from '@/shared/api/schema';
import {SLOT_TYPE_ICONS} from './slotIcons';
import {slotTypeOf, SLOT_TYPE_LABELS} from './specDraft';
import {parseAnchors} from './defaultText';
import { CursorMenu, CursorMenuItem, CursorMenuGroup, type CaretPoint } from './CursorMenu';

/**
 * 「设为槽位默认描述」的小菜单。
 *
 * ## 它解决什么
 *
 * 作者把一段测好的提示词封装成模板时，那段描述（比如「一望无际的雪原」）
 * 要同时是两件事：
 * - 提示词正文的一部分（作者写的时候就在那儿）；
 * - 使用者不填素材时的兜底（= 槽位的「未选时文案」）。
 *
 * 以前要把这段**剪切**到槽位卡片里另写一遍。现在框选正文里的一段，
 * 从这个菜单里挑一个槽位，它就同时是正文和那个槽位的兜底了。
 *
 * ## 为什么菜单列的是**可选**槽位
 *
 * 只有可选槽位有「没填素材」这个状态 —— 必填槽位一定是直接插编号
 * （后端 `rules._validate_slot_texts` 连两段文案都不许写）。
 * 把必填槽位列出来，作者挑一个就会在保存时被 400 打回。
 *
 * ## 为什么菜单在选区旁边
 *
 * 作者的注意力在那段文字上。菜单浮在别处（面板顶部、槽位卡片里）
 * 就得再找一次 —— 而「刚才框的是哪段」这件事必须靠位置来传达。
 */
export function DefaultTextMenu({
  slots,
  range,
  point,
  prompt,
  onPick,
  onCancel,
  onDismiss,
}: {
  slots: SpecSlotIn[];
  /** 选区（带锚点标记的正文坐标）。null = 不显示。 */
  range: {start: number; end: number} | null;
  /** 选区右下角的视口坐标：菜单浮在框选的那段字旁边。 */
  point?: CaretPoint | null;
  /** 当前正文：用来算选中的那段文字、也用来判断某个槽位是不是已经有默认描述了。 */
  prompt: string;
  /** 选中一个槽位：把选中的那段设为它的默认描述。 */
  onPick: (slotKey: string, range: {start: number; end: number}) => void;
  /** 取消某槽位的默认描述：摘标记、文字留在正文。 */
  onCancel: (slotKey: string) => void;
  /** 关闭菜单（点别处 / 按 Esc）。 */
  onDismiss: () => void;
}) {
  /* 只有可选、用户填的槽位能被指派：
     - 必填槽位没有「没填」这个状态（提示词里直接插编号）；
     - 固定槽位的素材在封装时就定死了，也不存在「没填」。 */
  const candidates = slots.filter(
    slot => (slot.minCount ?? 1) === 0 && (slot.binding ?? 'user') === 'user',
  );

  if (!range || candidates.length === 0) return null;

  /* 框选落在某段已有默认描述里 → 找出是哪个槽位。给一项「取消」，
     让指派可逆：作者设错了能就地撤回，不用去槽位卡片找。 */
  const anchored = parseAnchors(prompt).find(
    (span) => span.start <= range.start && range.end <= span.end + 6,
  );
  const anchoredSlot = anchored && slots.find((slot) => slot.key === anchored.slotKey);

  return (
    <CursorMenu point={range ? point ?? null : null} onDismiss={onDismiss}>
      <CursorMenuGroup label="设为默认描述（不填素材时用这段）">
        {candidates.map(slot => {
          const Icon = SLOT_TYPE_ICONS[slotTypeOf(slot.type)];
          const name = slot.label?.trim() ||
            `${SLOT_TYPE_LABELS[slotTypeOf(slot.type)]} ${(slot.position ?? 0) + 1}`;
          const has = Boolean(slot.emptyText?.trim());
          return (
            <CursorMenuItem
              key={slot.key}
              label={name}
              hint={has ? '已有描述' : undefined}
              icon={Icon && <Icon size={12} />}
              onSelect={() => onPick(slot.key, range)}
            />
          );
        })}
      </CursorMenuGroup>
      {anchoredSlot && (
        <div className="mt-1 border-t border-line-soft pt-1">
          <CursorMenuItem
            label={`取消「${anchoredSlot.label?.trim() || anchoredSlot.key}」的默认描述`}
            hint="文字留在正文"
            onSelect={() => onCancel(anchoredSlot.key)}
          />
        </div>
      )}
    </CursorMenu>
  );
}

