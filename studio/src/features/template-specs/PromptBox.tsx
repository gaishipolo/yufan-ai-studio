import {useMemo, useRef} from 'react';
import {cn} from '@/shared/lib/cn';
import {type SlotType} from './specDraft';
import {SLOT_TYPE_ICONS} from './slotIcons';
import {
  MentionTextarea,
  type MentionCandidate,
  type MentionTextareaHandle,
} from './MentionTextarea';

export interface PromptChip {
  token: string;
  label: string;
  all: boolean;
  /** 槽位类型：画图标用 */
  kind?: SlotType;
}

/**
 * 提示词输入框：**支持 `@` 提及槽位**。
 *
 * 两条插入路径完全等价（写进提示词的都是同一个 `{{slot:key}}` token）：
 * - 在框里打 `@` → 弹候选 → 选中（鼠标点 / ↑↓ + Enter）；
 * - 点下方 chip → 插到当前光标处。
 *
 * 输入框本体是共享的 `MentionTextarea`（槽位的「选中时文案」框也用它，
 * 两处的候选面板行为才不会漂移）。
 *
 * **没有「预览」块**：原来输入框下面另有一块，把正文里的 `{{slot:key}}`
 * 渲染成插槽名，好让作者看见「哪几段会被素材顶掉」。那块被取消了 ——
 * 它下面那排 chip 本来就把可用槽位全列了一遍（还是可点的），
 * 再并排画一份「同样的胶囊、只是不能点」等于同一件事说两遍。
 * 视觉语言沿用预览块那套：类型图标 + 蓝底胶囊（多值槽位是紫的）。
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
                  'inline-flex items-center gap-1 rounded-[5px] border px-1.5 py-px text-micro font-medium transition-colors',
                  chip.all
                    ? 'border-violet/30 bg-violet/10 text-violet hover:bg-violet/15'
                    : 'border-primary-line bg-primary-soft text-primary-deep hover:bg-primary/15',
                )}
              >
                {Icon && <Icon size={11} className="shrink-0" />}
                {chip.label}
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
