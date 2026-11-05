import { create } from 'zustand';
import { GUEST_SUBJECT, type Subject } from './types';

/* ------------------------------------------------------------------
 * 会话（身份）状态 —— 骨架里唯一的「当前用户」出口
 *
 * 三态，别合并成布尔量：
 *   loading        有令牌但没有身份快照，正拿 /identity/me 换身份。
 *                  这一态必须存在：否则刷新页面会先闪一下登录页再跳回来。
 *   authenticated  已登录。可能来自快照（后台仍在刷新），权限判定够用。
 *   anonymous      未登录 / 令牌过期 / 被 401 清空。
 *
 * 登录模块只调用 signIn / applyRefreshedTokens / setSubject / signOut，
 * 路由守卫、导航过滤、<Can>、顶栏会自动跟着变。
 *
 * 写入口都在这里，因此两个不变量只需要在这里守住：
 *   1. `authenticated ⇒ token !== null`（`setSubject` / `applyRefreshedTokens` 都查令牌）；
 *   2. 清空时不留残余（access 令牌、刷新令牌、身份快照三者一起清）。
 * 这两条以前靠调用方自觉，结果就是「退出后迟到的 /me 把界面又点亮」。
 * ------------------------------------------------------------------ */

export const TOKEN_STORAGE_KEY = 'sf_token';
export const SUBJECT_STORAGE_KEY = 'sf_subject';
export const TOKEN_EXPIRES_STORAGE_KEY = 'sf_token_expires_at';
/**
 * 刷新令牌与它的到期时刻。**不放进 Subject、也不进内存以外的任何地方**：
 * 它换不出业务数据，但能换出新 access 令牌，所以和 access 令牌同级对待，
 * 一起写入、一起清空（见 `persistToken` / `persistRefreshToken`）。
 */
export const REFRESH_TOKEN_STORAGE_KEY = 'sf_refresh_token';
export const REFRESH_EXPIRES_STORAGE_KEY = 'sf_refresh_expires_at';

export type SessionStatus = 'loading' | 'anonymous' | 'authenticated';

/* localStorage 在隐私模式 / 被策略禁用时会直接抛错。
   写失败就退化为「仅内存会话」：本次可用，刷新后需重新登录，好过白屏。 */
function readStorage(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* 忽略：见上方注释 */
  }
}

/** 快照可能来自旧版本或被手工改过，逐字段校验后再用；坏数据一律当没有 */
function parseSubject(raw: string | null): Subject | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<Subject> | null;
    if (!parsed || typeof parsed.id !== 'string' || !parsed.id) return null;
    if (!Array.isArray(parsed.permissions)) return null;
    return {
      id: parsed.id,
      name: typeof parsed.name === 'string' && parsed.name ? parsed.name : parsed.id,
      roles: Array.isArray(parsed.roles) ? parsed.roles.filter((r) => typeof r === 'string') : [],
      permissions: parsed.permissions.filter((p) => typeof p === 'string'),
    };
  } catch {
    return null;
  }
}

/** 读一个「毫秒时间戳」类的值；没有 / 坏数据一律 null（不要退化成 0，0 会被当成"已过期"） */
function readTimestamp(key: string): number | null {
  const raw = Number(readStorage(key) ?? '0');
  return Number.isFinite(raw) && raw > 0 ? raw : null;
}

/**
 * 只清 access 令牌与身份快照，**留住**刷新令牌。
 *
 * 这是「access 过期但会话还没到期」的中间态：用户关掉页面几小时再回来，
 * access 早已过期，可 refresh 还有几天。这时若连 refresh 一起清掉，
 * 后端给的 7 天会话在**最需要它的场景**（页面关着的那段时间）等于不存在。
 * 清了快照是因为换票成功前身份不可信——留着会让界面带着旧权限直接点亮。
 */
function clearAccessToken() {
  writeStorage(TOKEN_STORAGE_KEY, null);
  writeStorage(TOKEN_EXPIRES_STORAGE_KEY, null);
  writeStorage(SUBJECT_STORAGE_KEY, null);
}

/** 落盘令牌与过期时刻，并把 expiresAt 返回给调用方写进内存态 */
function persistToken(token: string | null, expiresIn?: number): number | null {
  writeStorage(TOKEN_STORAGE_KEY, token);
  if (!token) {
    writeStorage(TOKEN_EXPIRES_STORAGE_KEY, null);
    writeStorage(SUBJECT_STORAGE_KEY, null);
    writeStorage(REFRESH_TOKEN_STORAGE_KEY, null);
    writeStorage(REFRESH_EXPIRES_STORAGE_KEY, null);
    return null;
  }
  const expiresAt =
    typeof expiresIn === 'number' && expiresIn > 0 ? Date.now() + expiresIn * 1000 : null;
  writeStorage(TOKEN_EXPIRES_STORAGE_KEY, expiresAt === null ? null : String(expiresAt));
  return expiresAt;
}

/**
 * 刷新令牌的落盘。与 access 令牌分开一个函数只是因为两者寿命不同：
 * access 2 小时、refresh 7 天且服务端滑动续期，混在一个"过期时刻"里会互相覆盖。
 */
function persistRefreshToken(refreshToken: string | null, expiresIn?: number): number | null {
  writeStorage(REFRESH_TOKEN_STORAGE_KEY, refreshToken);
  if (!refreshToken) {
    writeStorage(REFRESH_EXPIRES_STORAGE_KEY, null);
    return null;
  }
  const expiresAt =
    typeof expiresIn === 'number' && expiresIn > 0 ? Date.now() + expiresIn * 1000 : null;
  writeStorage(REFRESH_EXPIRES_STORAGE_KEY, expiresAt === null ? null : String(expiresAt));
  return expiresAt;
}

function persistSubject(subject: Subject) {
  writeStorage(SUBJECT_STORAGE_KEY, JSON.stringify(subject));
}

/**
 * 启动时同步恢复会话。
 *
 * 「access 令牌不能用」有两种，必须分开处理 —— 混在一起会让 7 天会话退化成 2 小时：
 *
 *   a. **access 过期，但刷新令牌还活着**（关掉页面几小时再回来、手机锁屏、过夜）。
 *      只清 access 与快照，**留住刷新令牌**，状态停在 `loading`，
 *      由 `SessionBootstrap` 先换票再取身份。这条路径是刷新令牌唯一有用的场合——
 *      `useSessionExpiryWatch` 管的是「页面开着」，而页面关着时没有任何定时器在跑。
 *
 *   b. **两把令牌都过期 / 根本没有刷新令牌**：那才是真的没救，全清并记下原因
 *      （登录页据此提示「登录已过期」，而不是静默弹表单）。
 *
 * 还有第三种边界：上次冷启动正在换票时用户又刷新了一次页面 ——
 * 此时 localStorage 里只有 refreshToken 没有 access，等价于 (a)，同样进 `loading`。
 */
function resolveInitialSession(): {
  status: SessionStatus;
  subject: Subject;
  token: string | null;
  expiresAt: number | null;
  refreshToken: string | null;
  refreshExpiresAt: number | null;
  signOutReason: SignOutRecord | null;
} {
  const token = readStorage(TOKEN_STORAGE_KEY);
  const refreshToken = readStorage(REFRESH_TOKEN_STORAGE_KEY);
  const refreshExpiresAt = readTimestamp(REFRESH_EXPIRES_STORAGE_KEY);
  const refreshUsable =
    Boolean(refreshToken) && (refreshExpiresAt === null || Date.now() < refreshExpiresAt);

  const expiresAt = readTimestamp(TOKEN_EXPIRES_STORAGE_KEY);
  const accessExpired = expiresAt !== null && Date.now() >= expiresAt;

  if (!token || accessExpired) {
    if (refreshUsable) {
      clearAccessToken();
      return {
        status: 'loading',
        subject: GUEST_SUBJECT,
        token: null,
        expiresAt: null,
        refreshToken,
        refreshExpiresAt,
        signOutReason: null,
      };
    }

    // 从来没有登录过：什么都不用清，也不必说"过期了"
    if (!token && !refreshToken) return blank('anonymous');

    persistToken(null);
    return blank('anonymous', 'expired');
  }

  const cached = parseSubject(readStorage(SUBJECT_STORAGE_KEY));
  if (cached) {
    return {
      status: 'authenticated',
      subject: cached,
      token,
      expiresAt,
      refreshToken,
      refreshExpiresAt,
      signOutReason: null,
    };
  }

  // 有令牌但没有快照：先 loading，等 /identity/me 回来再定性
  return {
    status: 'loading',
    subject: GUEST_SUBJECT,
    token,
    expiresAt,
    refreshToken,
    refreshExpiresAt,
    signOutReason: null,
  };
}

function blank(status: SessionStatus, reason: SignOutReason | null = null) {
  return {
    status,
    subject: GUEST_SUBJECT,
    token: null,
    expiresAt: null,
    refreshToken: null,
    refreshExpiresAt: null,
    signOutReason: reason ? { kind: reason, at: Date.now() } : null,
  };
}

export interface SignInPayload {
  token: string;
  /** 秒；对应后端 TokenOut.expiresIn。不传则不过期，由服务端 401 兜底 */
  expiresIn?: number;
  /** 刷新令牌；后端 `TokenOut.refreshToken`。没有它就只能等 401 再回登录页 */
  refreshToken?: string;
  /** 秒；刷新令牌有效期，服务端每次续期都会滑动延长 */
  refreshExpiresIn?: number;
  subject: Subject;
}

/** 续期成功后的入参：只换票，身份与缓存都不动 */
export interface RefreshedTokensPayload {
  token: string;
  expiresIn?: number;
  refreshToken?: string;
  refreshExpiresIn?: number;
}

/**
 * 会话是怎么结束的——只用于登录页措辞，不参与任何权限判断。
 *
 * - `manual`       用户点了退出，不该再解释什么；
 * - `expired`      令牌到期（本地时钟判定），需要告诉用户"过期了"；
 * - `unauthorized` 服务端回 401（令牌被吊销 / 服务器密钥轮换 / 时钟偏差）；
 * - `external`     另一个标签页退出登录，本页跟着清场；
 * - `password`     本人刚改过密码。后端 `change_password` 会吊销**全部**会话
 *                  （`revoke_all(reason="password_change")`），当前这台也不例外，
 *                  所以这不是意外掉线，得说清楚"是因为你改了密码"。
 */
export type SignOutReason = 'manual' | 'expired' | 'unauthorized' | 'external' | 'password';

export interface SignOutRecord {
  kind: SignOutReason;
  /** 发生时刻：登录页只认「刚发生的」原因，避免很久以后误报过期 */
  at: number;
}

/**
 * 原因的有效期。守卫只把「这么久之内发生的」登出原因转交给登录页，
 * 否则用户退出后过一小时再手动打开 /login，还会被告知「登录已过期」。
 */
export const SIGN_OUT_REASON_TTL_MS = 10_000;

interface SessionState {
  status: SessionStatus;
  subject: Subject;
  token: string | null;
  /**
   * 令牌到期时刻（毫秒）。null 表示「不知道」——后端没给 expiresIn 时不要瞎猜，
   * 交给 401 兜底；此时也不会启动本地过期计时器。
   */
  expiresAt: number | null;
  /** 刷新令牌。null 表示这次登录没拿到（旧版本存的会话、或后端未开启续期） */
  refreshToken: string | null;
  /** 刷新令牌到期时刻（毫秒）。null 表示不知道，照样尝试续期 */
  refreshExpiresAt: number | null;
  signOutReason: SignOutRecord | null;
  /** 登录成功后写入：令牌与身份一起落盘，避免刷新后状态撕裂 */
  signIn: (payload: SignInPayload) => void;
  /**
   * 续期成功：只替换令牌与到期时刻。
   * 身份不动——`/identity/refresh` 不返回用户信息，权限变更靠下一次 `/identity/me`。
   */
  applyRefreshedTokens: (payload: RefreshedTokensPayload) => void;
  /** 刷新用户信息 / 修改本人资料后回写；同步更新快照与全局可读身份 */
  setSubject: (subject: Subject) => void;
  /**
   * 跟随另一个标签页换过的令牌（它在同一时刻续了期）。
   * 返回是否真的换了——没换说明不是同一个人 / 存储里也没有更新的票。
   */
  adoptStoredTokens: () => boolean;
  /** 退出登录、401、令牌失效：清空一切。原因只影响登录页提示 */
  signOut: (reason?: SignOutReason) => void;
}

const INITIAL = resolveInitialSession();

export const useSessionStore = create<SessionState>()((set, get) => ({
  status: INITIAL.status,
  subject: INITIAL.subject,
  token: INITIAL.token,
  expiresAt: INITIAL.expiresAt,
  refreshToken: INITIAL.refreshToken,
  refreshExpiresAt: INITIAL.refreshExpiresAt,
  signOutReason: INITIAL.signOutReason,

  signIn: ({ token, expiresIn, refreshToken, refreshExpiresIn, subject }) => {
    const expiresAt = persistToken(token, expiresIn);
    const refreshExpiresAt = persistRefreshToken(refreshToken ?? null, refreshExpiresIn);
    persistSubject(subject);
    set({
      status: 'authenticated',
      subject,
      token,
      expiresAt,
      refreshToken: refreshToken ?? null,
      refreshExpiresAt,
      signOutReason: null,
    });
  },

  applyRefreshedTokens: ({ token, expiresIn, refreshToken, refreshExpiresIn }) => {
    const { token: currentToken, refreshToken: currentRefreshToken } = get();

    // 「会话还在」有两个证据：手上还有 access，或者还有刷新令牌（冷启动换票）。
    // 两个都没有才是真的结束了 —— 迟到的续期结果一律丢弃，否则会把已结束的会话"复活"。
    if (!currentToken && !currentRefreshToken) return;

    const expiresAt = persistToken(token, expiresIn);
    const nextRefreshToken = refreshToken ?? currentRefreshToken;
    const refreshExpiresAt = refreshToken
      ? persistRefreshToken(refreshToken, refreshExpiresIn)
      : get().refreshExpiresAt;

    set({
      // 冷启动（原本来没有 access）时身份还没到手：继续 loading，等 /me 回来再由
      // setSubject 点亮。直接置 authenticated 会让导航与 <Can> 带着空 subject
      // 渲染一帧 —— 用户看到的是"权限全丢了"。
      status: currentToken ? 'authenticated' : 'loading',
      token,
      expiresAt,
      refreshToken: nextRefreshToken,
      refreshExpiresAt,
    });
  },

  setSubject: (subject) => {
    // 同一条不变量：没有令牌就没有会话。`/me` 的 staleTime 是 5 分钟，
    // 重连/聚焦都可能让它在一个已经退出的页面上迟到——那时写回快照会同时造成
    // 「界面复活」和「localStorage 里留下上一个人的身份」，两件都不该发生。
    const { token, subject: current } = get();
    if (!token) return;

    // 还要确认「写进来的是不是当前这个人」。换号场景里 A 的 `/me` 可能迟到
    // （先退出 A、再登录 B，A 的响应此时才回来）：令牌是 B 的，subject 却是 A 的，
    // 界面就会带着 B 的令牌显示 A 的权限。
    // 例外是"从占位身份补全"——冷启动时 subject 是 GUEST，那不是"另一个人"。
    if (current.id !== GUEST_SUBJECT.id && subject.id !== current.id) return;

    persistSubject(subject);
    set({ subject, status: 'authenticated' });
  },

  adoptStoredTokens: () => {
    const current = get();
    const token = readStorage(TOKEN_STORAGE_KEY);
    if (!token || token === current.token) return false;

    // 换账号不跟：另一个标签页登的是别人，跟过去会带着本页的查询缓存。
    const stored = parseSubject(readStorage(SUBJECT_STORAGE_KEY));
    if (stored && stored.id !== current.subject.id) return false;

    const expiresAt = Number(readStorage(TOKEN_EXPIRES_STORAGE_KEY) ?? '0');
    if (Number.isFinite(expiresAt) && expiresAt > 0 && Date.now() >= expiresAt) return false;

    const refreshToken = readStorage(REFRESH_TOKEN_STORAGE_KEY);
    const refreshExpiresAt = Number(readStorage(REFRESH_EXPIRES_STORAGE_KEY) ?? '0');
    set({
      status: 'authenticated',
      token,
      expiresAt: expiresAt > 0 ? expiresAt : null,
      refreshToken,
      refreshExpiresAt: refreshExpiresAt > 0 ? refreshExpiresAt : null,
    });
    return true;
  },

  signOut: (reason = 'manual') => {
    persistToken(null);
    set({
      status: 'anonymous',
      subject: GUEST_SUBJECT,
      token: null,
      expiresAt: null,
      refreshToken: null,
      refreshExpiresAt: null,
      signOutReason: { kind: reason, at: Date.now() },
    });
  },
}));
