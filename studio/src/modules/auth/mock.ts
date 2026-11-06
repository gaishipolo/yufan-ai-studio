import { HttpError, type MockContext, type MockRoute } from '@/mocks/kernel';
import { checkEmail, checkPassword } from '@/entities/user/model/validation';

/* ------------------------------------------------------------------
 * 认证 · mock
 *
 * 把后端 `modules/identity` 的认证与「本人资料」六条接口在内存里复现：
 *   POST   /identity/login
 *   GET    /identity/me
 *   PATCH  /identity/me
 *   POST   /identity/me/password
 *   POST   /identity/refresh     （轮换刷新令牌）
 *   POST   /identity/logout      （作废当前会话，幂等）
 *
 * 与 user-access 的 mock 是两份数据（模块之间不允许互相 import），
 * 但账号、密码、角色刻意保持一致，切到真实后端时不需要改前端。
 * 真实后端下以 `api/src/seed.py` 的种子账号为准。
 *
 * 令牌是假的：`mock-token:<username>:<时间戳>:<随机>`，/me 靠它认人；
 * 刷新令牌是 `mock-refresh:<username>:<jti>`，一次有效、用过即换（与后端轮换同口径）。
 * 因此 mock 模式也能真实演练「登录 → 2 小时到点 → 续期 → 401 → 回登录页」的完整链路。
 * ------------------------------------------------------------------ */

interface MockRoleBrief {
  id: string;
  code: string;
  name: string;
  isSuperuser: boolean;
}

interface MockAccount {
  id: string;
  username: string;
  password: string;
  displayName: string;
  email: string | null;
  avatarUrl: string | null;
  isActive: boolean;
  lastLoginAt: string | null;
  createdAt: string;
  roles: MockRoleBrief[];
  permissions: string[];
}

const ROLE_ADMIN: MockRoleBrief = {
  id: 'r_admin',
  code: 'admin',
  name: '超级管理员',
  isSuperuser: true,
};
const ROLE_EDITOR: MockRoleBrief = {
  id: 'r_editor',
  code: 'editor',
  name: '内容编辑',
  isSuperuser: false,
};
const ROLE_VIEWER: MockRoleBrief = {
  id: 'r_viewer',
  code: 'viewer',
  name: '只读成员',
  isSuperuser: false,
};

const CONTENT_PERMISSIONS = [
  'catalog:read',
  'image:create',
  'image:read',
  'shot:read',
  'storyboard:read',
  'task:create',
  'task:read',
  'task:update',
  'template:read',
  'template:review',
  'asset:create',
  'asset:read',
];

const accounts: MockAccount[] = [
  {
    id: 'u_001',
    username: 'admin',
    password: 'admin@12345',
    displayName: '老大',
    email: 'boss@example.com',
    avatarUrl: null,
    isActive: true,
    lastLoginAt: null,
    createdAt: '2026-01-01T00:00:00.000Z',
    roles: [ROLE_ADMIN],
    permissions: ['*'],
  },
  {
    id: 'u_002',
    username: 'editor',
    password: 'editor@12345',
    displayName: '小林',
    email: null,
    avatarUrl: null,
    isActive: true,
    lastLoginAt: null,
    createdAt: '2026-01-08T00:00:00.000Z',
    roles: [ROLE_EDITOR],
    permissions: CONTENT_PERMISSIONS,
  },
  {
    id: 'u_003',
    username: 'viewer',
    password: 'viewer@12345',
    displayName: '阿 May',
    email: null,
    avatarUrl: null,
    isActive: true,
    lastLoginAt: null,
    createdAt: '2026-01-12T00:00:00.000Z',
    roles: [ROLE_VIEWER],
    permissions: [
      'asset:read',
      'catalog:read',
      'image:read',
      'shot:read',
      'storyboard:read',
      'task:read',
      'template:read',
    ],
  },
];

/* 与后端的 TTL 对齐：access 2 小时、refresh 7 天（滑动续期）。
 * 数字对齐才有意义 —— 前端「到点续期」的逻辑只有在 mock 里也会到点时才会被跑到。 */
const ACCESS_TTL_SECONDS = 2 * 60 * 60;
const REFRESH_TTL_SECONDS = 7 * 24 * 60 * 60;

const asText = (value: unknown): string => (typeof value === 'string' ? value : '');

function toMe(account: MockAccount) {
  return {
    id: account.id,
    username: account.username,
    displayName: account.displayName,
    email: account.email,
    avatarUrl: account.avatarUrl,
    isActive: account.isActive,
    lastLoginAt: account.lastLoginAt,
    createdAt: account.createdAt,
    roles: account.roles,
    permissions: account.permissions,
  };
}

/* ------------------------------------------------------------------
 * 会话：一张表管「谁的刷新令牌现在有效」，一个集合管「哪些 access 已作废」
 *
 * 只用一个 Map<username, jti> 就够：后端也是「一条会话一个当前 jti」，
 * 刷新即轮换，旧 jti 立刻失效（重放只会被拒，不会连带吊销整条会话）。
 * ------------------------------------------------------------------ */
const refreshJti = new Map<string, string>();
const revokedTokens = new Set<string>();

const newJti = () => Math.random().toString(36).slice(2, 10);

const issueAccessToken = (username: string) =>
  `mock-token:${username}:${Date.now()}:${newJti()}`;

const issueRefreshToken = (username: string) => {
  const jti = newJti();
  refreshJti.set(username, jti);
  return `mock-refresh:${username}:${jti}`;
};

/** 校验刷新令牌：必须是**当前**那一张，旧的自然换不出东西 */
function usernameOfRefreshToken(token: string): string | null {
  const [prefix, username, jti] = token.split(':');
  if (prefix !== 'mock-refresh' || !username || !jti) return null;
  return refreshJti.get(username) === jti ? username : null;
}

/* 登录失败限流：与后端 `identity/throttle.py` 同口径（5 次失败后第 6 次起 429，
 * 窗口 15 分钟，按用户名计数）。mock 里复现它是为了让「被锁定」这个前端分支
 * 在本地就能点出来，而不是只能等真后端拒绝时才第一次见到。
 *
 * 刻意不做「锁定期间连正确密码也拒绝」：后端明确允许正确密码通过，
 * 否则任何人用几个错密码就能把某个账号锁死。 */
const MAX_LOGIN_FAILURES = 5;
const LOGIN_WINDOW_MS = 15 * 60_000;
const LOGIN_LOCKED_MESSAGE = '密码错误次数过多，请 15 分钟后再试';
const loginFailures = new Map<string, number[]>();

function recentFailures(username: string): number[] {
  const now = Date.now();
  const list = (loginFailures.get(username) ?? []).filter((at) => now - at < LOGIN_WINDOW_MS);
  if (list.length) loginFailures.set(username, list);
  else loginFailures.delete(username);
  return list;
}

/** 记一次失败并返回窗口内的失败总数 */
function recordLoginFailure(username: string): number {
  const list = [...recentFailures(username), Date.now()];
  loginFailures.set(username, list);
  return list.length;
}


function accountOf(ctx: MockContext): MockAccount {
  const header = ctx.headers.authorization ?? '';
  const token = header.startsWith('Bearer ') ? header.slice('Bearer '.length).trim() : '';
  // 登出后那张票必须立刻失效，否则 mock 模式下「退出登录」看起来没生效
  if (revokedTokens.has(token)) throw new HttpError(401, '登录状态已失效，请重新登录');
  const username = token.split(':')[1];

  const account = accounts.find((item) => item.username === username);
  if (!account) throw new HttpError(401, '登录状态已失效，请重新登录');
  if (!account.isActive) throw new HttpError(401, '账号已被停用，请联系管理员');
  return account;
}

export const routes: MockRoute[] = [
  {
    method: 'POST',
    pattern: /^\/identity\/login$/,
    handler: (ctx) => {
      const username = asText(ctx.body.username).trim().toLowerCase();
      const password = asText(ctx.body.password);

      if (!username || !password) throw new HttpError(422, '请输入用户名和密码');

      const account = accounts.find((item) => item.username === username);
      // 与后端口径一致：不区分「用户不存在」与「密码错误」，避免泄露账号是否存在
      if (!account || account.password !== password) {
        // 先记录再判定：第 6 次失败才回 429，前 5 次仍是 401（用户需要知道"密码错了"）
        if (recordLoginFailure(username) > MAX_LOGIN_FAILURES) {
          throw new HttpError(429, LOGIN_LOCKED_MESSAGE);
        }
        throw new HttpError(401, '用户名或密码错误');
      }
      if (!account.isActive) throw new HttpError(403, '账号已停用，请联系管理员');

      loginFailures.delete(username);
      account.lastLoginAt = new Date().toISOString();

      return {
        accessToken: issueAccessToken(account.username),
        tokenType: 'Bearer',
        expiresIn: ACCESS_TTL_SECONDS,
        refreshToken: issueRefreshToken(account.username),
        refreshExpiresIn: REFRESH_TTL_SECONDS,
        user: toMe(account),
      };
    },
  },
  {
    method: 'POST',
    pattern: /^\/identity\/refresh$/,
    handler: (ctx) => {
      const username = usernameOfRefreshToken(asText(ctx.body.refreshToken));
      // 与后端口径一致：签名坏、已轮换、已吊销都归同一个 401，前端只需要一种结局
      if (!username) throw new HttpError(401, '登录状态已失效，请重新登录');

      const account = accounts.find((item) => item.username === username);
      if (!account || !account.isActive) throw new HttpError(401, '登录状态已失效，请重新登录');

      return {
        accessToken: issueAccessToken(account.username),
        tokenType: 'Bearer',
        expiresIn: ACCESS_TTL_SECONDS,
        // 轮换：这张发出去，旧的那张在 `issueRefreshToken` 里已经被顶掉
        refreshToken: issueRefreshToken(account.username),
        refreshExpiresIn: REFRESH_TTL_SECONDS,
        user: toMe(account),
      };
    },
  },
  {
    method: 'POST',
    pattern: /^\/identity\/logout$/,
    handler: (ctx) => {
      /* 幂等：没有令牌、令牌已作废都算成功。
         与后端一样刻意**不校验**令牌有效性 —— 用户第二次点退出不该看到报错。 */
      const header = ctx.headers.authorization ?? '';
      const token = header.startsWith('Bearer ') ? header.slice('Bearer '.length).trim() : '';
      const username = token.split(':')[1];

      if (token) revokedTokens.add(token);
      if (username) refreshJti.delete(username);
      return null;
    },
  },
  {
    method: 'GET',
    pattern: /^\/identity\/me$/,
    handler: (ctx) => toMe(accountOf(ctx)),
  },
  {
    method: 'PATCH',
    pattern: /^\/identity\/me$/,
    handler: (ctx) => {
      const account = accountOf(ctx);
      const body = ctx.body;

      /* 只更新「显式出现」的字段，与后端 PATCH 的 exclude_unset 语义一致 */
      if ('displayName' in body) {
        account.displayName = asText(body.displayName).trim().slice(0, 64);
      }
      if ('email' in body) {
        const email = body.email === null ? null : asText(body.email).trim();
        const emailError = checkEmail(email);
        if (emailError) throw new HttpError(422, emailError);
        account.email = email || null;
      }
      if ('avatarUrl' in body) {
        account.avatarUrl = body.avatarUrl === null ? null : asText(body.avatarUrl).trim() || null;
      }

      return toMe(account);
    },
  },
  {
    method: 'POST',
    pattern: /^\/identity\/me\/password$/,
    handler: (ctx) => {
      const account = accountOf(ctx);
      const oldPassword = asText(ctx.body.oldPassword);
      const newPassword = asText(ctx.body.newPassword);

      if (account.password !== oldPassword) throw new HttpError(400, '当前密码不正确');
      const passwordError = checkPassword(newPassword, '新密码');
      if (passwordError) throw new HttpError(422, passwordError);
      if (newPassword === oldPassword) {
        throw new HttpError(422, '新密码不能与当前密码相同');
      }

      account.password = newPassword;
      return null;
    },
  },
];
