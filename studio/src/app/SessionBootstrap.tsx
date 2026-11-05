import { useEffect, useState, type ReactNode } from 'react';
import { useMe } from '@/entities/session/api/session.queries';
import { SessionRecovery } from '@/shared/access/SessionRecovery';
import { endSession } from '@/shared/access/end-session';
import { refreshAccessToken } from '@/shared/access/token-refresh';
import {
  useSessionRefreshToken,
  useSessionStatus,
  useSessionToken,
} from '@/shared/access/use-session';
import { useSessionCrossTabSync, useSessionExpiryWatch } from '@/shared/access/use-session-watch';
import { ApiError, isUnauthorizedError } from '@/shared/api/types';

/**
 * 会话引导：把「本机存着的那点凭证」换成「可用的身份」。
 *
 * 放在 Provider 内部、路由之外，因此它对所有页面（含登录页）都生效。
 *
 * 进来时有三种起点，都要有出路：
 *
 *   1. **有 access 令牌**：拉一次 `/identity/me` 把权限补全。
 *      成功 → 回写 subject、store 从 loading 变 authenticated；
 *      401 → 拦截器已经清场，<RequireAuth> 送去登录页；
 *      其它失败（500 / 断网 / 超时）→ **必须在这里表态**，否则 status 永远停在
 *      loading，<RequireAuth> 一直转圈 —— 页面既不报错也不给出口（FE2-01）。
 *
 *   2. **只有刷新令牌**（关掉页面超过 2 小时再回来）：先换票再走第 1 条。
 *      access 令牌 2 小时、会话 7 天，这中间那段时间只有这一步能救。
 *      漏了它，`useMe(Boolean(token))` 的 enabled 是 false，查询永远不发，
 *      页面会死在 loading —— 与 FE2-01 是同一种症状，但成因完全不同。
 *
 *   3. **什么都没有**：直接渲染，守卫会把匿名用户送去登录页。
 *
 * 另外挂两个「会话自然结束」的监听（过期到点、他页登出）：
 * 它们必须常驻，而与具体页面无关，所以归这里而不是某个模块。
 */
export function SessionBootstrap({ children }: { children: ReactNode }) {
  const token = useSessionToken();
  const status = useSessionStatus();
  const refreshToken = useSessionRefreshToken();
  const me = useMe(Boolean(token));

  useSessionExpiryWatch();
  useSessionCrossTabSync();

  /* 冷启动换票：没有 access 但有刷新令牌。
     401 类失败（刷新令牌过期 / 会话被吊销 / 改过密码）结论明确 —— 结束会话回登录页；
     网络类失败不该把用户踢走（他连重新登录都做不到），改为给一个可重试的出口。 */
  const needsRefresh = status === 'loading' && !token && Boolean(refreshToken);
  const [refreshError, setRefreshError] = useState<ApiError | null>(null);
  const [refreshAttempt, setRefreshAttempt] = useState(0);

  useEffect(() => {
    if (!needsRefresh) return undefined;

    let alive = true;
    setRefreshError(null);

    void refreshAccessToken().catch((error: unknown) => {
      if (!alive) return;
      if (isUnauthorizedError(error)) {
        endSession('expired');
        return;
      }
      setRefreshError(error instanceof ApiError ? error : new ApiError('刷新登录状态失败', -1));
    });

    return () => {
      alive = false;
    };
  }, [needsRefresh, refreshAttempt]);

  const recovering = status === 'loading' && me.isError && !isUnauthorizedError(me.error);

  if (recovering) {
    return <SessionRecovery error={me.error} onRetry={() => void me.refetch()} />;
  }

  // 换票失败（网络类）：同样不假装还在加载，给重试 / 重新登录两个出口
  if (refreshError) {
    return (
      <SessionRecovery error={refreshError} onRetry={() => setRefreshAttempt((n) => n + 1)} />
    );
  }

  return <>{children}</>;
}
