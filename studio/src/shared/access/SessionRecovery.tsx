import { Button, ErrorState } from '@/shared/ui';
import { endSession } from './end-session';

/* ------------------------------------------------------------------
 * 会话恢复失败 —— 「引导」这件事的第三种结局
 *
 * 刷新页面时如果只有令牌没有身份快照（第一次进、刚清过缓存、
 * 或者 `Subject` 结构改过导致 parseSubject 一律返回 null），
 * store 会停在 `loading` 等 `/identity/me` 定性。
 *
 * 只用「成功」和「401」两种结局去覆盖它是不够的：
 * 500 / 502 / 断网 / 超时都不是 401，拦截器不会清场，`status` 永远停在 loading，
 * <RequireAuth> 就会一直渲染整页 loading —— 不跳登录页、不报错、不重试，
 * 用户唯一的出路是手动清 localStorage。
 *
 * 所以失败必须是一个**要被说出口**的状态：告诉用户发生了什么，
 * 并给出两个出口（重试 / 重新登录）。UI 直接复用 ErrorState，
 * 不新增一套错误视觉。
 * ------------------------------------------------------------------ */

export interface SessionRecoveryProps {
  error: unknown;
  onRetry: () => void;
}

export function SessionRecovery({ error, onRetry }: SessionRecoveryProps) {
  return (
    <div className="grid min-h-screen place-items-center bg-[linear-gradient(180deg,#edf4f5_0%,#e8f0f2_100%)] px-4">
      <div className="w-full max-w-md rounded-card border border-line-soft bg-panel px-4 pb-4 pt-5 shadow-card">
        <h1 className="text-center text-[14px] font-semibold text-ink">无法恢复登录状态</h1>
        <p className="mx-auto mt-1.5 max-w-xs text-center text-[11.5px] leading-[1.6] text-ink-mute">
          本机有登录凭证，但没能确认它属于谁。这通常是网络或服务端临时故障，
          重试一次多半就好；也可以直接重新登录。
        </p>

        {/* 具体原因交给 ErrorState 分类（超时 / 连不上 / 5xx），不在这里重写一套 */}
        <ErrorState
          error={error}
          onRetry={onRetry}
          className="flex flex-col items-center px-0 pb-4 pt-5 text-center"
        />

        <div className="flex justify-center border-t border-line-soft pt-3">
          {/* 退出后 <RequireAuth> 会把用户送进登录页；不带原因，因为这既非过期也非被动失效 */}
          <Button size="sm" variant="ghost" onClick={() => endSession('manual')}>
            重新登录
          </Button>
        </div>
      </div>
    </div>
  );
}
