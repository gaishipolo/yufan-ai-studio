import { create } from 'zustand';

/**
 * 提示色调：**信息 / 成功 / 警告 / 错误**。
 *
 * 与 `shared/ui/Notice.tsx` 的 `NoticeTone` 同一套词 —— 两边都叫「警告」，
 * 就不用在脑子里做一次映射。
 *
 * 以前这里叫 `default`（中性深灰），语义含糊：读代码的人看不出这一条是
 * 「随口说一句」还是「出了事」。现在叫 `info`，四种色调各自有图标与
 * 朗读前缀，不靠颜色单独承载信息。
 */
export type ToastTone = 'info' | 'success' | 'warning' | 'error';

/** 旧名保留为别名：`'default'` 就是 `'info'`，不打断已有调用 */
export type ToastToneInput = ToastTone | 'default';

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface ToastItem {
  id: number;
  message: string;
  tone: ToastTone;
  action?: ToastAction;
}

export interface ToastOptions {
  message: string;
  tone?: ToastToneInput;
  /** 可选的动作，如「查看任务」。带动作的会多给一点时间，否则来不及点。 */
  action?: ToastAction;
  /**
   * 去重键：同一个 key 再推一次是**替换**而不是堆叠。
   *
   * 连点三次「已保存」不该出现三条一样的提示（它们还会在同一刻一起消失，
   * 看起来像界面抖了一下）。有 key 时保持原位置替换，用户不会觉得列表在跳。
   */
  key?: string;
  /** 毫秒；0 = 不自动消失。不传则按色调取默认值。 */
  duration?: number;
}

interface ToastState {
  items: ToastItem[];
  push: (input: string | ToastOptions, tone?: ToastToneInput) => void;
  dismiss: (id: number) => void;
}

const DEFAULT_DURATION = 2200;
const WITH_ACTION_DURATION = 4500;

/**
 * 分色调的驻留时长。
 *
 * 成功与信息一句话就能读完，2.2 秒够；**警告与错误不一样**：它们常常带着
 * 「为什么」和「怎么办」，2.2 秒只够扫一眼就消失 —— 用户等于没看见。
 * 所以这两类留得久，还带关闭按钮（见 `Toaster.tsx`）。
 */
const TONE_DURATION: Record<ToastTone, number> = {
  info: DEFAULT_DURATION,
  success: DEFAULT_DURATION,
  warning: 5000,
  error: 8000,
};

let seq = 0;

/** 每条的定时器；替换或手动关闭时要一起清掉，否则会提前掐掉新的那条 */
const timers = new Map<number, number>();
/** key → 当前那条的 id，用于「同 key 替换」 */
const idsByKey = new Map<string, number>();

function clearTimer(id: number) {
  const timer = timers.get(id);
  if (timer === undefined) return;
  window.clearTimeout(timer);
  timers.delete(id);
}

function forget(id: number) {
  clearTimer(id);
  for (const [key, value] of idsByKey) {
    if (value === id) idsByKey.delete(key);
  }
}

/** `'default'` 是旧名，归到 `'info'` */
function normalizeTone(tone: ToastToneInput | undefined): ToastTone {
  if (tone === 'default' || tone === undefined) return 'info';
  return tone;
}

export const useToastStore = create<ToastState>((set) => {
  const remove = (id: number) => {
    forget(id);
    set((state) => ({ items: state.items.filter((item) => item.id !== id) }));
  };

  const schedule = (id: number, duration: number) => {
    if (duration <= 0) return; // 0 = 驻留，交给用户关或点动作
    timers.set(
      id,
      window.setTimeout(() => remove(id), duration),
    );
  };

  return {
    items: [],

    push: (input, tone) => {
      const options: ToastOptions = typeof input === 'string' ? { message: input } : input;
      const nextTone = normalizeTone(options.tone ?? tone);
      const duration =
        options.duration ??
        (options.action
          ? Math.max(WITH_ACTION_DURATION, TONE_DURATION[nextTone])
          : TONE_DURATION[nextTone]);

      const existing = options.key ? idsByKey.get(options.key) : undefined;
      if (existing !== undefined) {
        clearTimer(existing);
        set((state) => ({
          items: state.items.map((item) =>
            item.id === existing
              ? { ...item, message: options.message, tone: nextTone, action: options.action }
              : item,
          ),
        }));
        schedule(existing, duration);
        return;
      }

      const id = ++seq;
      if (options.key) idsByKey.set(options.key, id);
      set((state) => ({
        items: [
          ...state.items,
          { id, message: options.message, tone: nextTone, action: options.action },
        ],
      }));
      schedule(id, duration);
    },

    dismiss: remove,
  };
});

/**
 * 任意位置调用。四种写法等价：
 *
 * ```ts
 * toast('已保存');
 * toast('已保存', 'success');
 * toast('已保存', 'error');
 * toast({message: '任务已提交', tone: 'success', key: 'task:1',
 *        action: {label: '查看任务', onClick: () => navigate('/tasks')}});
 * ```
 *
 * ## 什么场景用哪一种
 *
 * | 场景 | 用法 | 理由 |
 * | --- | --- | --- |
 * | 表单提交成功 | `toast('已保存', 'success')` | 一句话回执，不打断后续操作 |
 * | 表单提交失败 | `toast(msg, 'error')` | 错误要看得见、读得完，所以留 8 秒且带关闭按钮 |
 * | 校验没过 / 前置条件不满足 | `toast(msg, 'warning')` 或就地 `Notice` | 字段旁的错误就地显示更好定位 |
 * | 需要用户确认 | **不要用 toast** | 用 `shared/ui/ConfirmDialog`：确认是决策，不是通知 |
 * | 中性交代一句 | `toast(msg)`（= info） | 不成功也不失败，只是说明发生了什么 |
 *
 * 需要「先确认再执行」的地方一律走 `ConfirmDialog`：它是页面内的弹层，
 * 能把这次会发生什么摊开写清楚，还能带上提醒条 —— 原生 `window.confirm`
 * 做不到这些，还会把页面卡住。
 */
export const toast = (input: string | ToastOptions, tone?: ToastToneInput) =>
  useToastStore.getState().push(input, tone);
