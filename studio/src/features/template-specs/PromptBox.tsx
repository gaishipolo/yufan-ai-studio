import {useMemo, type ReactNode} from 'react';
import {cn} from '@/shared/lib/cn';
import {type SlotType} from './specDraft';
import {SLOT_TYPE_ICONS} from './slotIcons';
import {
  MentionTextarea,
  type MentionCandidate,
  type MentionTextareaHandle,
} from './MentionTextarea';
import {useRef} from 'react';

export interface PromptChip {
  token: string;
  label: string;
  all: boolean;
  /** 槽位类型：画图标用 */
  kind?: SlotType;
}

/**
 * 正文里的素材占位长什么样：`{{slot:key}}`（键是内部 id，作者看不懂）。
 *
 * **两边允许空格**（`{{ slot:key }}`）：后端与 `specDraft` 都是容空格版本，
 * 这里写成严格版就会漏掉老草稿里的带空格引用——预览里它只是一段普通文字。
 */
const SLOT_TOKEN = /\{\{\s*slot:([a-z][a-z0-9_]{0,39})(?:\*|\.\d+)?\s*\}\}/g;

/**
 * 提示词输入框：**支持 `@` 提及槽位**。
 *
 * 两条插入路径完全等价（写进提示词的都是同一个 `{{slot:key}}` token）：
 * - 在框里打 `@` → 弹候选 → 选中（鼠标点 / ↑↓ + Enter）；
 * - 点下方 chip → 插到当前光标处。
 *
 * 输入框本体是共享的 `MentionTextarea`（槽位的「选中时文案」框也用它，
 * 两处的候选面板行为才不会漂移）。这里多出来的是：预览 + 下方 chip 行。
 */
export function PromptBox({
  value,
  chips,
  onChange,
  onInsert,
}: {
  value: string;
  chips: PromptChip[];
  onChange: (text: string) => void;
  /** 程序写入引用后的回调（chip 按钮 / `@` 提及共用）。 */
  onInsert: (text: string) => void;
}) {
  /* 下方 chip 按钮插入引用：插到当前光标处（`@` 提及是另一条路，两者等价）。
     光标位置由输入框自己记着（父组件拿不到），所以走它的 imperative handle。 */
  const boxRef = useRef<MentionTextareaHandle>(null);

  const candidates = useMemo<MentionCandidate[]>(
    () => chips.map(chip => ({...chip})),
    [chips],
  );

  /* 提示词预览：**正文里的素材占位显示成「插槽名」**，与普通文字分开。

     textarea 里只能是原始 token（`{{slot:image_1}}` 这种内部 id，作者读不懂），
     而这一段是「这段话长什么样」的读法：占位是一枚带类型图标的 chip，普通文字是纯文字。
     看一眼就知道哪几段会被素材顶掉。 */
  const preview = useMemo(() => {
    const parts: ReactNode[] = [];
    let last = 0;
    for (const match of value.matchAll(SLOT_TOKEN)) {
      const at = match.index ?? 0;
      if (at > last) parts.push(value.slice(last, at));
      const token = match[0];
      /* 按键找 chip，不按原文找：带空格的引用写法与 chip 里的 token 不同。 */
      const chip = chips.find(item => item.token === `{{slot:` + (match[1] ?? '') + `}}`);
      const Icon = chip?.kind ? SLOT_TYPE_ICONS[chip.kind] : undefined;
      parts.push(
        <span
          key={`${token}-${at}`}
          title={token}
          className={cn(
            'mx-0.5 inline-flex translate-y-[1px] items-center gap-1 rounded-[5px] border px-1.5 py-px align-baseline text-[0.95em] font-medium',
            chip?.all
              ? 'border-violet/30 bg-violet/10 text-violet'
              : 'border-primary-line bg-primary-soft text-primary-deep',
          )}
        >
          {Icon && <Icon size={11} className="shrink-0" />}
          {chip?.label ?? token}
        </span>,
      );
      last = at + token.length;
    }
    if (last < value.length) parts.push(value.slice(last));
    return parts;
  }, [value, chips]);

  /* 下方 chip 按钮插入引用：插到当前光标处（`@` 提及是另一条路，两者等价）。 */
  const insertAtCaret = (token: string) => {
    boxRef.current?.insertToken(token);
  };

  return (
    <div>
      <MentionTextarea
        ref={boxRef}
        value={value}
        candidates={candidates}
        rows={10}
        placeholder="写提示词；打 @ 可以直接选槽位"
        className="rounded-xl px-3 py-2.5"
        onChange={text => {
          onChange(text);
        }}
        onInsert={onInsert}
      >
        {/* 预览：作者写的是 token，看到的是插槽名 */}
        <div
          data-testid="prompt-preview"
          className="mt-2 rounded-xl border border-line-soft bg-panel-soft/60 px-3 py-2"
        >
          <div className="mb-1 text-micro text-ink-mute">预览（素材占位显示插槽名）</div>
          <div className="max-h-[110px] overflow-y-auto scroll-thin text-xs leading-[1.9] text-ink-soft">
            {value.trim() ? (
              preview
            ) : (
              <span className="text-ink-mute">还没有提示词</span>
            )}
          </div>
        </div>
      </MentionTextarea>

      {/* chip 行降到 z-0：候选面板浮在上面时，它不该把面板压出一道缺口。
          有了 @ 之后它是**辅助**路径（有人不知道能打 @），所以做小做淡，
          不跟提示词正文抢注意力。
          「打 @ 引用」这行说明删掉了：输入框的占位符里已经写着
          「打 @ 可以直接选槽位」，再说一遍就是同一句话讲两遍。 */}
      <div className="relative z-0 mt-2 flex flex-wrap items-center gap-1.5">
        {chips.length === 0 ? (
          <span className="text-meta text-ink-mute">先在左边加槽位，再打 @ 引用</span>
        ) : (
          chips.map(chip => {
            const Icon = chip.kind ? SLOT_TYPE_ICONS[chip.kind] : undefined;
            return (
              <button
                key={chip.token}
                type="button"
                title={`插入 ${chip.token}（点一下插到光标处）`}
                onClick={() => insertAtCaret(chip.token)}
                className={cn(
                  'inline-flex items-center gap-1 rounded-lg border px-1.5 py-0.5 text-micro transition-colors',
                  chip.all
                    ? 'border-violet/25 bg-violet/10 text-violet hover:bg-violet/15'
                    : 'border-line-soft bg-panel-soft text-ink-soft hover:border-primary-line hover:text-primary',
                )}
              >
                {Icon && <Icon size={11} />}
                {chip.label}
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
