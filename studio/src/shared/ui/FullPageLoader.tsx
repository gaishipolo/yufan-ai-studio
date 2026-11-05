import { Loader2 } from 'lucide-react';
import { cn } from '@/shared/lib/cn';

/**
 * 整页加载态：用于「还不能确定该渲染登录页还是应用」的瞬间。
 * 不套 AppShell（此时导航与身份都可能无效），所以是自带背景的独立层。
 */
export function FullPageLoader({ label = '加载中…' }: { label?: string }) {
  return (
    <div
      className="grid min-h-screen place-items-center bg-[linear-gradient(180deg,#edf4f5_0%,#e8f0f2_100%)]"
      role="status"
      aria-live="polite"
    >
      <div className="flex flex-col items-center gap-3">
        <Loader2 size={22} className={cn('animate-spin text-primary')} aria-hidden />
        <p className="text-xs text-ink-mute">{label}</p>
      </div>
    </div>
  );
}
