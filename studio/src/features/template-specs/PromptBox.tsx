import {useMemo, useRef} from 'react';
import {cn} from '@/shared/lib/cn';
import {type SlotType} from './specDraft';
import {SLOT_TYPE_ICONS} from './slotIcons';
import {
  type MentionCandidate,
  type MentionTextareaHandle,
} from './MentionTextarea';
import {RichPromptEditor} from './RichPromptEditor';
import type {CaretPoint} from './CursorMenu';

export interface PromptChip {
  token: string;
  label: string;
  all: boolean;
  /** 槽位类型：画图标用 */
  kind?: SlotType;
  /** 作者定死的素材（`binding === 'fixed'`）：chip 上标「已固定」而不是「用户填写」 */
  fixed?: boolean;
}

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
  slotNames,
  onChange,
  onInsert,
  onSelectionChange,
  placeholder = '写提示词；打 @ 可以直接选槽位',
  ariaLabel = '提示词',
}: {
  value: string;
  chips: PromptChip[];
  /** 槽位 key → 展示名，给正文里的默认描述段加 hover 气泡。 */
  slotNames?: Record<string, string>;
  /** 用户改动。返回 `false` 表示**被拒**（父组件校验引用），输入框会把界面回退。 */
  onChange: (text: string) => void | boolean;
  /** 程序写入引用后的回调（chip 按钮 / `@` 提及 / 粘贴共用）。 */
  onInsert: (text: string) => void | boolean;
  /** 选区变化：父组件用它弹「设为槽位默认描述」的菜单。 */
  onSelectionChange?: (
    range: {start: number; end: number} | null,
    point?: CaretPoint | null,
  ) => void;
  /** 占位符：同一组件在两处用时，只有它该说清「这一格是干什么的」。 */
  placeholder?: string;
  ariaLabel?: string;
}) {
  /* 下方 chip 按钮插入引用：插到当前光标处（`@` 提及是另一条路，两者等价）。
     光标位置由输入框自己记着（父组件拿不到），所以走它的 imperative handle。 */
  const boxRef = useRef<MentionTextareaHandle>(null);

  const candidates = useMemo<MentionCandidate[]>(
    () => chips.map(chip => ({...chip})),
    [chips],
  );

  /* 下方 chip 按钮插入引用：插到当前光标处（`@` 提及是另一条路，两者等价）。 */
  const insertAtCaret = (token: string) => {
    boxRef.current?.insertToken(token);
  };

  return (
    <div>
      {/* 富文本输入框：正文里的 `{{slot:key}}` 直接渲染成胶囊，
          作者看到的就是「这里会被一张正面图顶掉」，而不是内部 id。
          原先下面另有一块「预览」干这件事，取消 —— 输入框本身已经是预览了。 */}
      <RichPromptEditor
        ref={boxRef}
        value={value}
        candidates={candidates}
        slotNames={slotNames}
        aria-label={ariaLabel}
        placeholder={placeholder}
        className="rounded-xl px-3 py-2.5"
        onChange={onChange}
        onInsert={onInsert}
        onSelectionChange={onSelectionChange}
      />

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
                  'inline-flex items-center gap-1.5 rounded-pill border px-2 py-1 text-meta transition-colors',
                  'border-line-soft bg-panel-soft text-ink-soft',
                  'hover:border-primary-line hover:text-primary',
                )}
              >
                {/* 视觉沿用原「槽位速览」那一枚：绿点 = 作者定死、蓝点 = 使用者挑。
                    那块板块取消后，这个区分只剩这里能看见 —— 而它对写提示词的人
                    是有用的（固定素材不可能变，用户填的每张成片都不一样，
                    提示词里该不该写死细节取决于它）。 */}
                <span
                  className={cn(
                    'size-1.5 shrink-0 rounded-full',
                    chip.fixed ? 'bg-ok' : 'bg-primary',
                  )}
                />
                {Icon && <Icon size={11} className="shrink-0 text-ink-mute" />}
                <span className="truncate">{chip.label}</span>
                <span className="text-micro text-ink-mute">
                  {chip.fixed ? '已固定' : '用户填写'}
                </span>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
