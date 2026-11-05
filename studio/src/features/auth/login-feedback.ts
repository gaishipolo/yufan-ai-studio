import { ApiError, errorHttpStatus } from '@/shared/api/types';

/**
 * 登录失败的分类与措辞。
 *
 * 为什么不在表单里直接展示 `err.message`：登录是唯一「匿名可达」的接口，
 * 也是唯一会出现「不是你的错」的失败（被限流、后端挂了、网络断了）。
 * 三种情况都甩一句后端原文，用户会以为是自己密码记错了，然后继续试——
 * 而继续试正是限流想要阻止的动作。
 *
 * 分类依据是**状态段**而不是完整业务码（见 `errorHttpStatus`）：
 * 后端登录失败是 identity 模块的 40121，不是框架的 40100，写死具体码必然失效。
 *
 * 后端登录限流的实际行为（`modules/identity/throttle.py`）：
 * 前 5 次失败回 401，第 6 次起回 429 且文案带「请 N 分钟后再试」。
 * 前端据此在锁定期间禁用提交并按秒倒计时，避免用户对着锁定按钮空点。
 *
 * 注意这是**滑动窗口**：解锁时刻由窗口内第一次失败决定，不再继续失败的话，
 * 真实解锁比「从现在起 N 分钟」更早。后端不返回剩余秒数，所以前端只能把
 * N 当作上限——文案因此写「最多再等 N」，而不是「请 N 后再试」。
 */
export interface LoginFailure {
  title: string;
  detail: string;
  /** 需要禁用提交的秒数；0 表示可以立即重试 */
  cooldownSec: number;
}

/** 后端文案里解析不出时长时的兜底等待：够短，不至于让用户以为页面卡死 */
const FALLBACK_COOLDOWN_SEC = 60;

/** 从「请 15 分钟后再试」里取时长；取不到就按兜底值，不会把表单永久锁死 */
function parseCooldownSec(message: string): number {
  const minutes = /(\d+)\s*分钟/.exec(message);
  if (minutes) {
    const value = Number(minutes[1]) * 60;
    if (Number.isFinite(value) && value > 0) return Math.min(value, 60 * 60);
  }
  const seconds = /(\d+)\s*秒/.exec(message);
  if (seconds) {
    const value = Number(seconds[1]);
    if (Number.isFinite(value) && value > 0) return Math.min(value, 60 * 60);
  }
  return FALLBACK_COOLDOWN_SEC;
}

/**
 * 把提示接在后端文案后面。后端消息末字有没有句号不确定，
 * 直接拼接会读出「用户名或密码错误 再连续输错…」这种半句话，所以补一个间隔。
 */
function appendHint(message: string, hint: string): string {
  const text = message.trim();
  const ended = /[。．.！!？?；;：:]$/.test(text);
  return `${text}${ended ? '' : '。'}${hint}`;
}

/**
 * @param error         useLogin 抛出的错误
 * @param failuresSoFar 本次表单里已经连续失败几次（含这一次）
 */
export function describeLoginFailure(error: unknown, failuresSoFar = 1): LoginFailure {
  if (!(error instanceof ApiError)) {
    return {
      title: '登录失败',
      detail: '发生了未知错误，请稍后重试',
      cooldownSec: 0,
    };
  }

  const status = errorHttpStatus(error.code);

  if (status === 429) {
    return {
      // 标题只说"发生了什么"，"还要等多久"由后端文案给出（里面带具体分钟数）
      title: '已被暂时锁定',
      detail: error.message || '密码错误次数过多，请稍后再试',
      cooldownSec: parseCooldownSec(error.message),
    };
  }

  if (status === 401) {
    return {
      // 标题是类别，详情是后端原文：两者不重复说同一句话
      title: '登录失败',
      // 后端对「账号不存在」和「密码错误」给同一句提示，前端照原样展示，不替它猜
      detail:
        failuresSoFar >= 3
          ? appendHint(error.message, '再连续输错可能会被暂时锁定，建议确认密码或联系管理员重置。')
          : error.message,
      cooldownSec: 0,
    };
  }

  if (status === 403) {
    // 登录阶段只有一种 403：账号被停用。重试没有意义，直接说清楚
    return { title: '账号不可用', detail: error.message, cooldownSec: 0 };
  }

  if (status === 408) {
    return { title: '请求超时', detail: '服务响应较慢，请稍后重试', cooldownSec: 0 };
  }

  if (status === -1) {
    return { title: '网络异常', detail: '无法连接服务，请检查网络后重试', cooldownSec: 0 };
  }

  if (status >= 500) {
    return {
      title: '服务异常',
      detail: error.message || '服务暂时不可用，请稍后重试',
      cooldownSec: 0,
    };
  }

  return { title: '登录失败', detail: error.message, cooldownSec: 0 };
}

/** 倒计时文案：`92` → `1 分 32 秒` */
export function formatCooldown(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s === 0 ? `${m} 分` : `${m} 分 ${s} 秒`;
}
