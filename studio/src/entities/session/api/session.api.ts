import { api } from '@/shared/api/http';
import type { Me } from '@/entities/user/model/types';
import type { LoginPayload, PasswordChangePayload, ProfilePatch, TokenResult } from '../model/types';

/**
 * 认证与本人资料接口（后端 `modules/identity/router.py` 的 `/identity` 前缀）。
 *
 * 这里只有四条「正常带令牌发就行」的接口。
 * 续期（`/identity/refresh`）与登出（`/identity/logout`）不在这里，
 * 原因不是漏了，而是它们的鉴权方式不同：续期用请求体里的刷新令牌，
 * 登出发生在本地清场之后、必须显式带上旧令牌，两者都不能走带拦截器的主干
 * （否则会自成递归）。它们住在 `shared/api/auth.ts` 的旁路通道里，
 * 调用方是 `shared/access/token-refresh` 与 `shared/access/end-session`。
 */
export const sessionApi = {
  login: (payload: LoginPayload) => api.post<TokenResult>('/identity/login', payload),

  me: () => api.get<Me>('/identity/me'),

  updateProfile: (patch: ProfilePatch) => api.patch<Me>('/identity/me', patch),

  /**
   * 改密会让此前签发的所有令牌失效（后端按 `password_changed_at` 判定，见
   * `identity/principal.py`），并且 `change_password` 里紧跟
   * `sessions.revoke_all(reason="password_change")`（R10-03）——**当前这台也被吊销**。
   *
   * 所以调用方（`features/auth/ChangePasswordModal`）成功后要主动
   * `endSession('password')`：等下一次请求 401 再被踢，中间那段时间界面还在假装
   * 一切正常，用户会以为是随机掉线。
   */
  changePassword: (payload: PasswordChangePayload) =>
    api.post<null>('/identity/me/password', payload),
};
