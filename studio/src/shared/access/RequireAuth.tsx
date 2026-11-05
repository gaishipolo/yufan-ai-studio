import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { LOGIN_PATH } from '@/shared/config/app';
import { FullPageLoader } from '@/shared/ui/FullPageLoader';
import { SIGN_OUT_REASON_TTL_MS, useSessionStore, type SignOutReason } from './session';
import { useSessionStatus } from './use-session';

/** 转交给登录页的历史状态：从哪来 + 为什么被赶出来 */
export interface LoginRedirectState {
  from?: string;
  reason?: SignOutReason;
}

/**
 * 登录守卫。由骨架的 router 工厂套在整个外壳外层，业务页面不用手写。
 *
 * 未登录时把「原本要去哪」放进 location.state.from，登录页成功后原路返回——
 * 这样从收藏夹直接打开深链接也不会被丢到首页。
 * 同时带上「会话为什么结束」，登录页据此给出「已过期 / 已失效」而不是默认文案。
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const status = useSessionStatus();
  const signOutReason = useSessionStore((state) => state.signOutReason);
  const location = useLocation();

  if (status === 'loading') return <FullPageLoader label="正在恢复登录状态…" />;

  if (status === 'anonymous') {
    const from = `${location.pathname}${location.search}`;
    // 主动退出的原因没有信息量，不往登录页传，省得退出后又弹一句「已退出」
    const isFresh = signOutReason && Date.now() - signOutReason.at < SIGN_OUT_REASON_TTL_MS;
    const reason = isFresh && signOutReason.kind !== 'manual' ? signOutReason.kind : undefined;
    const state: LoginRedirectState = reason ? { from, reason } : { from };

    return <Navigate to={LOGIN_PATH} replace state={state} />;
  }

  return <>{children}</>;
}
