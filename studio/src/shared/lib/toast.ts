import { create } from 'zustand';

export type ToastTone = 'default' | 'success' | 'warning' | 'error';

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
  tone?: ToastTone;
  /** 可选的动作，如「查看任务」。带动作的会多给一点时间，否则来不及点。 */
  action?: ToastAction;
  /**
   * 去重键：同一个 key 再推一次是**替换**而不是堆叠。
   *
   * 连点三次「已保存」不该出现三条一样的提示（它们还会在同一刻一起消失，
   * 看起来像界面抖了一下）。有 key 时保持原位置替换，用户不会觉得列表在跳。
   */
  key?: string;
  /** 毫秒；0 = 不自动消失。不传则按有没有 action 取默认值。 */
  duration?: number;
}

interface ToastState {
  items: ToastItem[];
  push: (input: string | ToastOptions, tone?: ToastTone) => void;
  dismiss: (id: number) => void;
}

const DEFAULT_DURATION = 2200;
const WITH_ACTION_DURATION = 4500;

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

    push: (input, tone = 'default') => {
      const options: ToastOptions = typeof input === 'string' ? { message: input } : input;
      const nextTone = options.tone ?? tone;
      const duration =
        options.duration ?? (options.action ? WITH_ACTION_DURATION : DEFAULT_DURATION);

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
 * 任意位置调用。三种写法等价：
 *
 * ```ts
 * toast('已保存');
 * toast('已保存', 'success');
 * toast({message: '任务已提交', tone: 'success', key: 'task:1',
 *        action: {label: '查看任务', onClick: () => navigate('/tasks')}});
 * ```
 */
export const toast = (input: string | ToastOptions, tone?: ToastTone) =>
  useToastStore.getState().push(input, tone);
