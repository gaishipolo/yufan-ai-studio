import { requestRefresh } from '@/shared/api/auth';
import { ApiError } from '@/shared/api/types';
import { useSessionStore } from './session';

/* ------------------------------------------------------------------
 * 续期 —— 全应用唯一的「换票」入口
 *
 * 三条规矩，缺一条就会变成 bug：
 *
 *   1. **单飞**。一屏同时有 6 个请求，令牌过期时它们会一起 401。
 *      六个并发续期里，第一个成功并轮换了 refresh 令牌，其余五个拿旧令牌
 *      必然失败（后端把旧 jti 判为无效）——用户会看到「明明还在用却突然退出」。
 *      所以并发调用共享同一个 Promise。
 *
 *   2. **失败先怀疑「别的标签页换过了」**。多标签页下两边会同时 401，
 *      后到的那边手里一定是旧 refresh。这时不能清场，去看 localStorage：
 *      如果那边的票更新，跟过去就行（`adoptStoredTokens` 会校验是同一个人）。
 *
 *   3. **不碰身份，也不碰路由**。续期只换令牌，权限等下一次 `/identity/me`；
 *      跳转仍由 `<RequireAuth>` 读 status 决定，这里不 import 路由。
 * ------------------------------------------------------------------ */

/** 正在进行的续期；null 表示当前没有。并发调用共享它 */
let inflight: Promise<string> | null = null;

async function runRefresh(): Promise<string> {
  const { refreshToken, refreshExpiresAt } = useSessionStore.getState();

  // 没有刷新令牌：旧版本存下的会话、或后端没返回。只能等 401 回登录页
  if (!refreshToken) throw new ApiError('没有刷新令牌，无法续期', 401);
  // 刷新令牌自己也过期了（长期不用）：不必发一个注定 401 的请求
  if (refreshExpiresAt && Date.now() >= refreshExpiresAt) {
    throw new ApiError('刷新令牌已过期，请重新登录', 401);
  }

  try {
    const tokens = await requestRefresh(refreshToken);

    /* 换票期间会话可能已经变了：用户点了退出、或在另一个账号上重新登录。
       此时手上这张新票属于**旧**会话，写进 store 就等于用旧账号的票覆盖新账号
       （或者把已结束的会话续回来）。判断依据是「我拿出去的那张刷新令牌还是不是当前的」，
       比只看 token 是否为空更严：换号之后 token 不为空，但已经不是同一张票了。 */
    if (useSessionStore.getState().refreshToken !== refreshToken) {
      throw new ApiError('会话已结束', 401);
    }

    useSessionStore.getState().applyRefreshedTokens({
      token: tokens.accessToken,
      expiresIn: tokens.expiresIn,
      refreshToken: tokens.refreshToken,
      refreshExpiresIn: tokens.refreshExpiresIn,
    });

    const current = useSessionStore.getState().token;
    if (!current) throw new ApiError('会话已结束', 401);
    return current;
  } catch (error) {
    // 跟过去之后必须**重新取一次** state：zustand 的 set 会换掉状态对象，
    // 用旧快照读到的还是那张已经失效的令牌
    if (useSessionStore.getState().adoptStoredTokens()) {
      const adopted = useSessionStore.getState().token;
      if (adopted) return adopted;
    }
    throw error;
  }
}

/**
 * 换一张有效的 access 令牌。成功返回新令牌，失败抛出原因。
 * 调用方负责在失败时清场（`endSession`）——这里不 import 它，避免与 http 形成环。
 */
export function refreshAccessToken(): Promise<string> {
  inflight ??= runRefresh().finally(() => {
    inflight = null;
  });
  return inflight;
}
