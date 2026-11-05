import { create } from 'zustand';

export type ToastTone = 'default' | 'success' | 'error';

export interface ToastItem {
  id: number;
  message: string;
  tone: ToastTone;
}

interface ToastState {
  items: ToastItem[];
  push: (message: string, tone?: ToastTone) => void;
  dismiss: (id: number) => void;
}

let seq = 0;

export const useToastStore = create<ToastState>((set) => ({
  items: [],
  push: (message, tone = 'default') => {
    const id = ++seq;
    set((state) => ({ items: [...state.items, { id, message, tone }] }));
    window.setTimeout(() => {
      set((state) => ({ items: state.items.filter((t) => t.id !== id) }));
    }, 2200);
  },
  dismiss: (id) => set((state) => ({ items: state.items.filter((t) => t.id !== id) })),
}));

/** 任意位置调用：toast('已保存') */
export const toast = (message: string, tone?: ToastTone) =>
  useToastStore.getState().push(message, tone);
