import { useEffect } from 'react';
import { endSession } from './end-session';
import { REFRESH_TOKEN_STORAGE_KEY, TOKEN_STORAGE_KEY, useSessionStore } from './session';
import { refreshAccessToken } from './token-refresh';

/* ------------------------------------------------------------------
 * 会话的两个「无人值守」生命周期事件
 *
 * 后端（`2fc0fc6`）有 `POST /identity/refresh`：access 令牌 2 小时，
 * 刷新令牌 7 天且滑动续期。所以到点不该直接登出，而是**先换票**：
 * 换到就继续用，换不到才结束会话。少这一步的后果很具体 ——
 * 每个用户上午、下午各掉一次线，而服务端本来愿意让他待 7 天。
 *
 * 「换不到」有四种可能，前端不区分（后端统一成 40122）：
 * 刷新令牌过期 / 会话被吊销（管理员踢下线）/ 口令改过 / 账号被停用。
 * 它们的结论一样：回登录页重新认证。
 * ------------------------------------------------------------------ */

/** setTimeout 的上限是 2^31-1 ms（约 24.8 天），更长的等待必须分段排 */
const MAX_TIMEOUT_MS = 2_147_483_647;

/**
 * 到点续期，续不上才登出。
 *
 * 只在 `expiresAt` 到点这一刻动手，不提前几分钟：提前续期会让「闲置 2 小时」
 * 的会话也始终活着，反而模糊了「活跃才延长」的语义；到点续期失败还有
 * 拦截器那条第二道保险（请求 401 → 续期 → 失败 → 清场），两条路共享同一个单飞。
 *
 * 标签页被浏览器休眠时 setTimeout 可能延后，这不是问题：
 * 醒来后 `left <= 0` 立刻处理。没有刷新令牌（旧版本存的会话）时退回原来的
 * 「如实结束会话」，不会假装续得上。
 */
export function useSessionExpiryWatch() {
  const status = useSessionStore((state) => state.status);
  const expiresAt = useSessionStore((state) => state.expiresAt);
  const refreshToken = useSessionStore((state) => state.refreshToken);

  useEffect(() => {
    if (status !== 'authenticated' || !expiresAt) return;

    let timer: ReturnType<typeof setTimeout> | undefined;
    let cancelled = false;

    const schedule = () => {
      const left = expiresAt - Date.now();
      if (left <= 0) {
        if (!refreshToken) {
          // 与 401 / 手动退出共用同一个出口：清会话 **和** 清查询缓存
          endSession('expired');
          return;
        }
        // 续期成功后 store 的 expiresAt 会变，本 effect 依赖它，会自己重排下一次；
        // 失败（或续期期间会话已结束）才清场。
        void refreshAccessToken().catch(() => {
          if (!cancelled) endSession('expired');
        });
        return;
      }
      timer = setTimeout(schedule, Math.min(left, MAX_TIMEOUT_MS));
    };

    schedule();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [status, expiresAt, refreshToken]);
}

/**
 * 多标签页同步：任一标签页**真的退出登录**，其余标签页立即一起退出。
 *
 * `storage` 事件只发给**其它**标签页，所以不会自触发；这里也只处理「被删」，
 * 不处理「被写成新值」——换号需要整页重载才能保证缓存干净，
 * 静默替换会留下上一个账号的查询缓存。
 *
 * 另一个标签页**续期**（同一个账号换成新票）也不在这里跟：那由
 * `refreshAccessToken` 在续期失败时用 `adoptStoredTokens` 接手，
 * 时机更准（正好是本页那张旧票被服务端判无效的那一刻）。
 *
 * **为什么主信号是「刷新令牌被销毁」而不是「access 令牌被删」**：
 * access 令牌会因为**到点**被单独清掉（`clearAccessToken`，冷启动换票前那一瞬间），
 * 那不是退出登录。以前只认 access 被删，于是别的标签页只是想换票，本页却
 * `endSession('external')` —— 而 `signOut` 会把刷新令牌一起清掉，
 * 7 天的会话就这样被一次无害的换票连坐销毁，用户被迫重新登录。
 * 现在：refresh 被删 = 真退出；只删 access 时，本页手里还有 refresh 就不动。
 *
 * 旧版本存下的会话没有刷新令牌，对它们来说「access 被删」仍是唯一可用的退出信号，
 * 所以保留那条分支，只是加了「本页没有 refresh 才跟」的前置条件。
 */
export function useSessionCrossTabSync() {
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.newValue !== null) return;
      const store = useSessionStore.getState();
      if (store.status === 'anonymous') return;

      if (event.key === REFRESH_TOKEN_STORAGE_KEY) {
        endSession('external');
        return;
      }

      if (event.key === TOKEN_STORAGE_KEY && !store.refreshToken) {
        endSession('external');
      }
    };

    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);
}
