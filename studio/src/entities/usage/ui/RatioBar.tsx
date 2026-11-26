import { cn } from '@/shared/lib/cn';

export interface RatioBarProps {
  /** 0-1 */
  value: number;
  className?: string;
  /** 强制指定语气；不给则按数值自动分档。 */
  tone?: 'ok' | 'primary' | 'warn';
}

/**
 * 单值比例条。
 *
 * **颜色是编码，不是装饰**：成功率到 90% 以上是绿、70% 以上是蓝、以下是黄 ——
 * 沿用仓库既有的语义色（`ok` / `primary` / `warn`），所以「这一格好不好」不需要
 * 一行说明文字去解释，看颜色就知道。
 *
 * 为什么不直接给数字就完事：数字要读，条要扫。一个成功率 68% 的账号，扫一眼
 * 那一格的颜色就知道「不太行」，再去读数字确认。
 */
export function RatioBar({ value, className, tone }: RatioBarProps) {
  const clamped = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
  const resolved = tone ?? (clamped >= 0.9 ? 'ok' : clamped >= 0.7 ? 'primary' : 'warn');

  return (
    <div className={cn('h-2 w-full overflow-hidden rounded-xs bg-line-soft', className)}>
      <div
        className={cn(
          'h-full rounded-xs transition-[width,background-color] duration-300 ease-smooth',
          resolved === 'ok' && 'bg-ok',
          resolved === 'primary' && 'bg-primary',
          resolved === 'warn' && 'bg-warn',
        )}
        style={{ width: `${clamped * 100}%` }}
      />
    </div>
  );
}
