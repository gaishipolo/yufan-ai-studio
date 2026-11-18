import type { ReactNode } from 'react';
import { X } from 'lucide-react';
import { cn } from '@/shared/lib/cn';

export type NoticeTone = 'info' | 'success' | 'warning' | 'error';

/**
 * 区域内的轻量提示条：表单下方、卡片里、弹窗底部那一句「成功 / 失败 / 请注意」。
 *
 * 和 toast 的分工：toast 是**一次性反馈**（2 秒后自己消失），Notice 是**状态**
 * （失败原因会一直挂着，直到下一次提交）。以前三处各写一份 `border-danger-line
 * bg-danger-soft` 的 div，内边距和文案换行已经开始不一致 —— 收在这里一份。
 *
 * 页面级错误（整页打不开）不用它，那是 `ErrorState`。
 */
const TONE_CLASS: Record<NoticeTone, string> = {
  info: 'border-line bg-panel-soft text-ink-soft',
  success: 'border-ok-line bg-ok-soft text-ok-deep',
  warning: 'border-warn-line bg-warn-soft text-warn-deep',
  error: 'border-danger-line bg-danger-soft text-danger',
};

export interface NoticeProps {
  tone?: NoticeTone;
  /** 可选的一行小标题，正文长的时候用它先给结论。可带图标（如「账户已锁定」）。 */
  title?: ReactNode;
  /** 传了才出现右上角的关闭按钮 */
  onClose?: () => void;
  className?: string;
  children: ReactNode;
}

export function Notice({
  tone = 'info',
  title,
  onClose,
  className,
  children,
}: NoticeProps) {
  return (
    <div
      /* 错误用 alert（打断），其余用 status（不打断读屏的当前朗读） */
      role={tone === 'error' ? 'alert' : 'status'}
      className={cn(
        'flex items-start gap-2 rounded-xl border px-3 py-2 text-meta leading-relaxed',
        TONE_CLASS[tone],
        className,
      )}
    >
      <div className="min-w-0 flex-1">
        {title && <p className="font-medium">{title}</p>}
        <div className="whitespace-pre-wrap break-words">{children}</div>
      </div>
      {onClose && (
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭提示"
          className="mt-0.5 shrink-0 rounded p-0.5 opacity-60 transition-opacity hover:opacity-100"
        >
          <X size={13} />
        </button>
      )}
    </div>
  );
}
