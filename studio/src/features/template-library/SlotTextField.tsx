import { cn } from '@/shared/lib/cn';

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
 * 初值是作者在封装页写的「未选时文案」——使用者改的是**那一段**，
 * 不是整段提示词（整段改写的入口 2026-11-14 已删，与「提示词只有一段」冲突）。
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
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  rows?: number;
  className?: string;
}) {
  return (
    <div className={cn('flex flex-col gap-1', className)}>
      <textarea
        value={value}
        rows={rows}
        maxLength={MAX_SLOT_TEXT}
        placeholder={placeholder}
        onChange={event => onChange(event.target.value)}
        className="w-full resize-y rounded-lg border border-line bg-white px-2.5 py-2 text-xs leading-[1.7] text-ink outline-none transition-colors placeholder:text-[#a9b8bf] hover:border-line-strong focus:border-primary-line focus:ring-2 focus:ring-primary/10"
      />
      <span className="self-end text-micro tabular-nums text-ink-mute">
        {value.length} 字
      </span>
    </div>
  );
}
