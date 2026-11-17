import { cn } from '@/shared/lib/cn';
import { useToastStore, type ToastTone } from '@/shared/lib/toast';

/**
 * 色调 → 底色。`success` / `warning` 以前和默认色一样（都是深灰），
 * 于是「成功」在视觉上等于「没消息」；补上颜色之后用户不读字也知道结果。
 */
const TONE_CLASS: Record<ToastTone, string> = {
  default: 'bg-[#1f2f38] text-white',
  success: 'bg-ok-deep text-white',
  warning: 'bg-warn-deep text-white',
  error: 'bg-danger text-white',
};

export function Toaster() {
  const items = useToastStore((s) => s.items);
  const dismiss = useToastStore((s) => s.dismiss);

  if (!items.length) return null;

  return (
    <div
      aria-live="polite"
      className="fixed left-1/2 top-3.5 z-[200] flex -translate-x-1/2 flex-col gap-2"
    >
      {items.map((item) => (
        <div
          key={item.id}
          role="status"
          className={cn(
            'animate-popIn flex max-w-[min(90vw,560px)] items-center gap-2 rounded-[9px] px-3.5 py-2 text-xs shadow-pop',
            TONE_CLASS[item.tone],
          )}
        >
          <span className="min-w-0 flex-1">{item.message}</span>
          {item.action && (
            <button
              type="button"
              onClick={() => {
                // 先关再跳：动作里通常带路由跳转，留着的话提示会跟着飘到新页面
                dismiss(item.id);
                item.action?.onClick();
              }}
              className="shrink-0 rounded-[5px] bg-white/15 px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap transition-colors hover:bg-white/25"
            >
              {item.action.label}
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
