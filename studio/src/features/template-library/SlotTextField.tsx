import { cn } from '@/shared/lib/cn';
import { RichPromptEditor } from '@/features/template-specs/RichPromptEditor';

/**
 * 后端 `MAX_SLOT_TEXT_OVERRIDE` 的同口径（`spec_service.py`）。
 *
 * 同一个槽位、同一段话，只是写的人不同（作者 vs 使用者），不该有两套限制。
 * 超了后端会 400，所以这里先挡住 —— 让人写到第 2001 个字才被拒，
 * 那 2000 个字还得自己再删一遍。
 */
export const MAX_SLOT_TEXT = 2000;

/**
 * 「改提示词」的输入框：这一格不挑素材，直接写提示词里那段话怎么说。
 *
 * 初值是作者在封装页写的「未选时文案」—— 使用者改的是**那一段**，
 * 不是整段提示词（整段改写的入口 2026-11-14 已删，与「提示词只有一段」冲突）。
 *
 * **用 `RichPromptEditor`（contenteditable）而不是 `textarea`**：作者写的那段话
 * 里可能有对别的槽位的引用（`{{Mixed 1}}`），textarea 只能显示成裸文本 ——
 * 使用者看到的是一串看不懂的花括号。换成编辑器后，引用照样画成胶囊。
 *
 * **不弹 @ 候选面板**（`candidates` 传空）：使用者改的是一段话，
 * 不是往提示词里插引用 —— 给他插引用的入口只会引出「引用了可选槽位」那类问题。
 *
 * 字数回读常驻：2000 字是**看得见才猜得到**的约束（不是重复说明），
 * 而它也确实会挡住提交。
 */
export function SlotTextField({
  value,
  onChange,
  placeholder,
  rows = 3,
  className,
  ariaLabel = '改提示词',
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  rows?: number;
  className?: string;
  /** 无障碍名：同一个槽位只有一个这样的框，但页面上可能有多个槽位。 */
  ariaLabel?: string;
}) {
  return (
    <div className={cn('flex flex-col gap-1', className)}>
      <RichPromptEditor
        value={value}
        candidates={[]}
        onChange={next => onChange(next.slice(0, MAX_SLOT_TEXT))}
        onInsert={next => onChange(next.slice(0, MAX_SLOT_TEXT))}
        aria-label={ariaLabel}
        placeholder={placeholder}
        className={cn(
          'w-full rounded-lg border border-line bg-panel px-2.5 py-2 text-xs leading-[1.7] text-ink outline-none transition-colors',
          'hover:border-line-strong focus:border-primary-line focus:ring-2 focus:ring-primary/10',
          /* contenteditable 没有原生 placeholder：空框用伪元素显示提示。 */
          'empty:before:content-[attr(data-placeholder)] empty:before:text-ink-mute',
          rows >= 3 ? 'min-h-[5.5rem]' : 'min-h-[4rem]',
        )}
      />
      <span className="self-end text-micro tabular-nums text-ink-mute">
        {value.length} 字
      </span>
    </div>
  );
}
