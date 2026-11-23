import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react';
import { cn } from '@/shared/lib/cn';
import { useToastStore, type ToastTone } from '@/shared/lib/toast';

/**
 * 一次性反馈的呈现层（toast）。
 *
 * ## 为什么不用浏览器原生弹窗
 *
 * 原生 `alert` / `confirm` 三个问题：**样式不可控**（各系统各一套，跟界面
 * 完全不是一回事）、**阻塞**（整个页面卡住，用户连滚动都做不了）、**不能
 * 分类**（成功和失败长得一样，只能靠文字区分）。
 *
 * 这里给出的是页面内的浮层：跟着设计系统的色板走、有进场动画、带图标，
 * 并且**不拦截交互**（容器 `pointer-events-none`，只有提示本身可点）。
 *
 * ## 四种色调
 *
 * | 色调 | 用在 | 停留 |
 * | --- | --- | --- |
 * | info（中性深灰） | 交代一句发生了什么 | 2.2s |
 * | success（绿） | 操作成功 | 2.2s |
 * | warning（琥珀） | 需要注意但没失败 | 5s |
 * | error（红） | 失败 | 8s |
 *
 * 颜色**不是唯一的信息载体**：每条都带图标，屏幕阅读器还会先念出
 * 「错误：」「警告：」这类前缀（`sr-only`），色觉障碍与读屏用户拿到的是
 * 同一份信息。
 */

/** 最多同时显示几条：再多就把页面盖住了（手机上尤其明显） */
const MAX_VISIBLE = 4;

const TONE_META: Record<
  ToastTone,
  {className: string; Icon: typeof Info; label: string}
> = {
  info: {className: 'bg-[#1f2f38] text-white', Icon: Info, label: '提示'},
  success: {className: 'bg-ok-deep text-white', Icon: CheckCircle2, label: '成功'},
  warning: {className: 'bg-warn-deep text-white', Icon: AlertTriangle, label: '警告'},
  error: {className: 'bg-danger text-white', Icon: XCircle, label: '错误'},
};

export function Toaster() {
  const items = useToastStore((s) => s.items);
  const dismiss = useToastStore((s) => s.dismiss);

  if (!items.length) return null;

  return (
    <div
      /* `pointer-events-none`：浮层不能挡住底下的操作 —— 提示是「顺带告诉
         一下」，不是「拦住你」。提示本身再单独开回来。 */
      className="pointer-events-none fixed left-1/2 top-3 z-[200] flex w-[min(92vw,560px)] -translate-x-1/2 flex-col gap-2 sm:top-3.5"
    >
      {items.slice(-MAX_VISIBLE).map((item) => {
        const {className, Icon, label} = TONE_META[item.tone];
        return (
          <div
            key={item.id}
            /* 错误用 alert（打断朗读），其余用 status（不打断读屏当前内容） */
            role={item.tone === 'error' ? 'alert' : 'status'}
            data-tone={item.tone}
            className={cn(
              'animate-popIn pointer-events-auto flex w-full items-start gap-2 rounded-[9px] px-3.5 py-2 text-xs shadow-pop',
              className,
            )}
          >
            <Icon size={13} className="mt-[3px] shrink-0 opacity-90" aria-hidden />
            <span className="min-w-0 flex-1">
              {/* 读屏先听到类型再听到内容：不靠颜色区分语义 */}
              <span className="sr-only">{label}：</span>
              {item.message}
            </span>
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
            <button
              type="button"
              aria-label="关闭提示"
              onClick={() => dismiss(item.id)}
              className="shrink-0 rounded-[4px] p-0.5 opacity-60 transition-opacity hover:opacity-100"
            >
              <X size={12} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
