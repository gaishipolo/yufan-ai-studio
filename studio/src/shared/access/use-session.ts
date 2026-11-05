import { useSessionStore } from './session';

/* ------------------------------------------------------------------
 * 会话状态的细粒度读取器
 *
 * 刻意与 use-access 分开：use-access 走 Context（可注入、便于单测），
 * 这里读 zustand store（全局唯一真相）。组件默认用 use-access，
 * 只有「需要感知未登录/加载中」的骨架组件才用这里的 hook。
 * ------------------------------------------------------------------ */

export const useSessionStatus = () => useSessionStore((state) => state.status);

export const useSessionToken = () => useSessionStore((state) => state.token);

/** 令牌到期时刻（毫秒）；null 表示后端未给 expiresIn，只靠 401 兜底 */
export const useSessionExpiresAt = () => useSessionStore((state) => state.expiresAt);

/**
 * 刷新令牌。正常使用时读它的人不多，只有「冷启动先换票」需要判断
 * 「没有 access、但有 refresh」这个中间态。
 */
export const useSessionRefreshToken = () => useSessionStore((state) => state.refreshToken);

export const useSignIn = () => useSessionStore((state) => state.signIn);

/* 这里刻意**没有** `useSignOut`。
 *
 * 「用户点退出」不是「把 store 清掉」：它还要清查询缓存（否则换账号后首屏看到
 * 上一个账号的数据）和通知服务端吊销会话。那件事在 `widgets/user-menu/use-sign-out`
 * 里，走 `endSession` 这个原子出口。
 *
 * 曾经这里也导出一个同名 hook（直接返回 store 的 signOut），零调用，但它是
 * 骨架的公开 API —— 下一个加「退出」入口的人最可能 import 的就是它，
 * 然后重新踩一遍刚修完的缓存串号。名字要留给用户意图，不留给底层动作。 */
