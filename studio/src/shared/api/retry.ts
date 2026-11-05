import { ApiError, errorHttpStatus } from './types';

/* ------------------------------------------------------------------
 * 查询重试策略 —— 只重试「下一次可能就好」的失败
 *
 * 为什么不能写 `retry: 1`：
 * 4xx 是**已经确定的结论**。401 会被拦截器清场，再重试一次只是多发一次必然失败的
 * 请求；403 / 404 密集的页面（权限不足、详情被删）会把请求数与日志量翻倍，
 * 排查时看到的是一片重试噪音，而不是真正的那一条 5xx。
 *
 * 可重试的三类：
 *   -1   连接不上（断网、后端没启动、代理挂了）—— 最常见，且多半是瞬时
 *   408  请求超时 —— axios 的 ECONNABORTED 在 http.ts 里归一成 408
 *   >=500 服务端故障
 * ------------------------------------------------------------------ */

/** 单次请求最多重试几次。1 次足够覆盖瞬时抖动，再多重试只会让用户多等 */
const MAX_RETRIES = 1;

export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  if (failureCount >= MAX_RETRIES) return false;
  // 不是接口错误（渲染异常、代码 bug）：重试不会变好，交给 ErrorBoundary
  if (!(error instanceof ApiError)) return false;

  const status = errorHttpStatus(error.code);
  return status === -1 || status === 408 || status >= 500;
}
