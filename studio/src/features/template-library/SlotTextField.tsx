import { useMemo, useRef, useState } from 'react';
import { cn } from '@/shared/lib/cn';
import { FORM_LIMITS } from '@/shared/lib/formLimits';
import { RichPromptEditor } from '@/features/template-specs/RichPromptEditor';
import type { RefChip } from '@/features/template-specs/specDraft';

/**
 * 后端 `MAX_SLOT_TEXT_OVERRIDE` 的同口径（`spec_service.py`）。
 *
 * 同一个槽位、同一段话，只是写的人不同（作者 vs 使用者），不该有两套限制。
 * 超了后端会 400，所以这里先挡住 —— 让人写到第 2001 个字才被拒，
 * 那 2000 个字还得自己再删一遍。
 */
export const MAX_SLOT_TEXT = FORM_LIMITS.slotText;

/**
 * 文字里的引用 token（作者写法 `{{Mixed N}}` 与内部写法 `{{slot:key}}`）。
 *
 * 后缀与后端 `rules._SLOT_REF` / `_MIXED_REF` 严格同形状：要么 `*` 单独，
 * 要么 `.` 加数字。写成 `[*.]\d*` 会把 `{{Mixed 1.}}` 这类畸形当合法 token
 * 保留下来，下一跳又被后端拒 —— 口径漂移在这里没有任何好处。
 */
const TOKEN_RE =
  /\{\{\s*(?:Mixed\s+\d+|slot:[a-z][a-z0-9_]{0,39})(?:\*|\.\d+)?\s*\}\}/g;

function tokensOf(text: string): string[] {
  return [...text.matchAll(TOKEN_RE)].map(match => match[0].replace(/\s+/g, ''));
}

/**
 * 「改提示词」的输入框：这一格不挑素材，直接写提示词里那段话怎么说。
 *
 * 初值是作者在封装页写的「未选时文案」—— 使用者改的是**那一段**，
 * 不是整段提示词（整段改写的入口 2026-11-14 已删，与「提示词只有一段」冲突）。
 *
 * **用 `RichPromptEditor`（contenteditable）而不是 `textarea`**：作者写的那段话
 * 里可能有对别的槽位的引用（`{{Mixed 1}}`），textarea 只能显示成裸文本 ——
 * 使用者看到的是一串看不懂的花括号。换成编辑器后，引用照样画成胶囊，
 * 胶囊上写的是**那个槽位的名字**（`chips` 传进来）。
 *
 * **使用者不能自己插引用**：作者写好的引用保持原样，但使用者新敲的
 * `{{Mixed n}}` 会被当成引用提交给后端 —— 那串编号对应哪个槽位使用者根本不知道，
 * 编号还随槽位顺序变（作者拖一下就指错）。所以新敲的引用一律拒掉。
 *
 * **不弹 @ 候选面板**：使用者改的是一段话，不是往提示词里插引用。
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
  chips = [],
  authorText,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  rows?: number;
  className?: string;
  /** 无障碍名：同一个槽位只有一个这样的框，但页面上可能有多个槽位。 */
  ariaLabel?: string;
  /** 胶囊上显示的槽位名：把整个模板的槽位传下来（引用可能指向别的槽位）。 */
  chips?: RefChip[];
  /**
   * 作者写的原文（该槽位的「未选时文案」）。
   *
   * 允许保留的引用以它为准 —— 模板或槽位一变它就跟着变，
   * 不依赖「组件什么时候挂载」这种不显式的约定。
   */
  authorText?: string;
}) {
  const [hint, setHint] = useState('');
  /* 允许保留的引用 = **作者写的那份**里的那些。
     不能拿「当前 value」算：父组件一接受上一步文本，新 token 就进了 value，
     下一轮它就成了「本来就有的」→ 拦不住。
     以前靠「首次挂载时捕获」—— 换模板时改写框会被卸载重挂载，所以碰巧对；
     那是不显式的约定：谁把改写框改成常驻渲染，就静默失效了。
     现在直接按作者原文算 —— 模板或槽位一变，这个集合就跟着变。 */
  const firstValue = useRef<string>(value);
  const allowedTokens = useMemo(
    () => new Set(tokensOf(authorText ?? firstValue.current)),
    [authorText],
  );
  const handleChange = (text: string) => {
    /* 允许保留的引用 = **当前这份**里已有的。作者写好的那些照旧，
       使用者新敲的一个都不许加。 */
    const added = tokensOf(text).filter(token => !allowedTokens.has(token));
    if (added.length) {
      setHint('这里不能插引用 —— 作者写好的引用会保持原样');
      return false; // 编辑器把 DOM 对齐回 value
    }
    setHint('');
    onChange(text.slice(0, MAX_SLOT_TEXT));
    return true;
  };
  return (
    <div className={cn('flex flex-col gap-1', className)}>
      <RichPromptEditor
        value={value}
        candidates={chips}
        allowMention={false}
        onChange={handleChange}
        onInsert={handleChange}
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
      {hint && <p className="text-micro text-warn-deep">{hint}</p>}
      <span className="self-end text-micro tabular-nums text-ink-mute">
        {value.length} 字
      </span>
    </div>
  );
}
