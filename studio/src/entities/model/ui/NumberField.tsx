import {useRef} from 'react';
import {TextInput} from '@/shared/ui';

export interface NumberFieldProps {
  id?: string;
  /** 字段的可访问名（label） */
  label: string;
  /** 当前值（数字或空串） */
  value: number | '';
  /** schema 声明的下限 */
  min?: number;
  /** schema 声明的上限 */
  max?: number;
  /** schema 声明的步长 */
  step?: number;
  onChange: (value: number | '') => void;
}

/**
 * 数字输入框：**先能改对，再谈好看**。
 *
 * ## 用户遇到的问题
 *
 * 「我设置 10，只能先输入 10 变成 5.10，然后 shift 移到前面把 5 删掉。」
 *
 * 根因是 **number 控件单击时把光标放在末尾**（实测真实 Chrome：单击后
 * `selectionStart = 1`，即光标在 `5` 之后），于是打新数字是**追加**：
 * `5` + `10` = `510`。
 *
 * ## 为什么 `onFocus` 里 select() 不够
 *
 * `onFocus` 确实触发、也确实选中了（实测 `onFocus` 里 `select()` 后
 * `selectionStart = 0`）。但**单击的默认行为会在 focus 之后把光标移回点击处**，
 * 把刚选上的选区取消掉 —— 于是用户接着打的还是追加。
 *
 * 实测（真实 Chrome，六种方案）：
 *
 * | 方案 | 结果 |
 * |---|---|
 * | `onfocus` 里 select | `510` ✗ |
 * | `onmouseup` 里 select | `10` ✓ |
 * | `onclick` 里 select | `10` ✓ |
 *
 * **`onClick` 在默认行为之后跑**，所以选区能留住。这是唯一可靠的时机。
 *
 * ## 三件事
 *
 * 1. **点击即全选**：用户接着打就是**替换**，不用先手动删旧值。
 *    「5 → 10」变成一步操作。
 * 2. **保留原生 number 控件**：上下箭头、`min`/`max` 的原生校验都在
 *    （实测 `type="number"` 的 `select()` 一样有效 —— 选区虽然
 *    `selectionStart` 是 null，但 `select()` 仍能把整串标记为选中，
 *    后续输入替换整串）。
 * 3. **失焦钳制**：失焦时把值钳到 schema 的 min/max，并向上层落定最终值。
 *    越界值留着只会在提交时被后端拒，而界面上看不出问题。
 *
 * ## 提交时机
 *
 * 输入过程中**不改**上层的值（除了合法的完整数字）。所以「输入 999」时
 * 上层还拿着 5，失焦才被钳到 30。这是有意的：
 * - 提前钳制 = 用户打字时看到数字自己跳走（「我明明打的 999」）；
 * - 半成品不该进业务状态。
 */
export function NumberField({
  id,
  label,
  value,
  min,
  max,
  step,
  onChange,
}: NumberFieldProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  const clamp = (raw: number): number => {
    let next = raw;
    if (min !== undefined && next < min) next = min;
    if (max !== undefined && next > max) next = max;
    return next;
  };

  /* 把「用户输入的这段文字」变成一个上报给上层的值。
     **上报的是原值，不是钳制后的值** —— 钳制发生在失焦时。
     输入过程中就把 999 改写成 30，用户会看到自己敲的数字凭空跳走
     （「我明明打的 999」），这比「提交时被拒」更让人困惑。 */
  const report = (raw: string) => {
    const trimmed = raw.trim();
    if (trimmed === '') {
      /* 空 = 不提交这个参数（后端用默认值）。见 `isEmptyParamValue`。 */
      onChange('');
      return;
    }
    const num = Number(trimmed);
    if (!Number.isFinite(num)) return; // 非法值不污染上层
    onChange(num);
  };

  return (
    <TextInput
      ref={inputRef}
      id={id}
      type="number"
      aria-label={label}
      {...(min !== undefined ? {min} : {})}
      {...(max !== undefined ? {max} : {})}
      {...(step !== undefined ? {step} : {})}
      value={value === '' ? '' : String(value)}
      onClick={event => {
        /* 点击即全选：接着打就是替换，不用先手动删旧值。
           必须在 **click** 而不是 focus：单击的默认行为会在 focus 之后
           把光标移回点击处，取消掉 focus 里刚选上的选区。 */
        event.currentTarget.select();
      }}
      onChange={event => report(event.target.value)}
      onBlur={event => {
        const raw = event.target.value.trim();
        /* 失焦：落定最终值（钳到 min/max）。 */
        if (raw === '') {
          onChange('');
          return;
        }
        const num = Number(raw);
        if (!Number.isFinite(num)) {
          onChange(value);
          return;
        }
        onChange(clamp(num));
      }}
    />
  );
}
