import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  REFRESH_TOKEN_STORAGE_KEY,
  REFRESH_EXPIRES_STORAGE_KEY,
  SUBJECT_STORAGE_KEY,
  TOKEN_EXPIRES_STORAGE_KEY,
  TOKEN_STORAGE_KEY,
} from './session';

/**
 * 会话 store 的启动分支最容易出回归：
 * 少判一个条件就会出现「刷新页面闪登录页」或「令牌过期了还在发请求」。
 * session.ts 在 import 时就执行初始化，所以每个用例都重新加载模块。
 */
async function loadSession() {
  vi.resetModules();
  return import('./session');
}

const subject = { id: 'u_1', name: '老大', roles: ['admin'], permissions: ['*'] };

describe('session store · 启动恢复', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('没有令牌 → 匿名', async () => {
    const { useSessionStore } = await loadSession();
    expect(useSessionStore.getState().status).toBe('anonymous');
    expect(useSessionStore.getState().token).toBeNull();
    expect(useSessionStore.getState().subject.id).toBe('anonymous');
  });

  /* FE4-01 的回归用例组：「access 过期」有两种，以前被合并成了一种。
     旧代码在这里 persistToken(null)，把刷新令牌一起删掉 —— 后端给的 7 天会话
     在「关掉页面几小时再回来」这条最常见路径上等于不存在。 */
  it('access 过期且没有刷新令牌 → 全清，回匿名并记住原因', async () => {
    localStorage.setItem(TOKEN_STORAGE_KEY, 'expired-token');
    localStorage.setItem(TOKEN_EXPIRES_STORAGE_KEY, String(Date.now() - 1_000));
    localStorage.setItem(SUBJECT_STORAGE_KEY, JSON.stringify(subject));

    const { useSessionStore } = await loadSession();

    expect(useSessionStore.getState().status).toBe('anonymous');
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
    expect(localStorage.getItem(SUBJECT_STORAGE_KEY)).toBeNull();
    // 记住"是过期走的"，登录页才能提示而不是静默弹表单
    expect(useSessionStore.getState().signOutReason?.kind).toBe('expired');
  });

  it('access 过期但刷新令牌还有效 → 留住它并进 loading（交给引导换票）', async () => {
    localStorage.setItem(TOKEN_STORAGE_KEY, 'expired-token');
    localStorage.setItem(TOKEN_EXPIRES_STORAGE_KEY, String(Date.now() - 3 * 60 * 60_000));
    localStorage.setItem(SUBJECT_STORAGE_KEY, JSON.stringify(subject));
    localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, 'still-valid-refresh');
    localStorage.setItem(REFRESH_EXPIRES_STORAGE_KEY, String(Date.now() + 6 * 24 * 60 * 60_000));

    const { useSessionStore } = await loadSession();

    const state = useSessionStore.getState();
    expect(state.status).toBe('loading');
    expect(state.token).toBeNull();
    // 过期的那把清掉，7 天的那把必须留下
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
    expect(localStorage.getItem(SUBJECT_STORAGE_KEY)).toBeNull();
    expect(localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY)).toBe('still-valid-refresh');
    expect(state.refreshToken).toBe('still-valid-refresh');
    // 快照清了，但身份换回来之前不该说"已过期"
    expect(state.signOutReason).toBeNull();
  });

  it('access 与刷新令牌都过期 → 才真的全清', async () => {
    localStorage.setItem(TOKEN_STORAGE_KEY, 'expired-token');
    localStorage.setItem(TOKEN_EXPIRES_STORAGE_KEY, String(Date.now() - 1_000));
    localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, 'expired-refresh');
    localStorage.setItem(REFRESH_EXPIRES_STORAGE_KEY, String(Date.now() - 1_000));
    localStorage.setItem(SUBJECT_STORAGE_KEY, JSON.stringify(subject));

    const { useSessionStore } = await loadSession();

    expect(useSessionStore.getState().status).toBe('anonymous');
    expect(useSessionStore.getState().refreshToken).toBeNull();
    expect(localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY)).toBeNull();
    expect(localStorage.getItem(REFRESH_EXPIRES_STORAGE_KEY)).toBeNull();
    expect(useSessionStore.getState().signOutReason?.kind).toBe('expired');
  });

  /* 上次冷启动正在换票时用户又刷新了一次页面：localStorage 里只剩 refresh。
     这条不该被当成"从未登录" —— 否则快速双击刷新就把会话丢了。 */
  it('只剩刷新令牌（换票途中又刷新）→ 同样是 loading，不是匿名', async () => {
    localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, 'rt');
    localStorage.setItem(REFRESH_EXPIRES_STORAGE_KEY, String(Date.now() + 60_000));

    const { useSessionStore } = await loadSession();

    expect(useSessionStore.getState().status).toBe('loading');
    expect(useSessionStore.getState().refreshToken).toBe('rt');
  });

  it('刷新令牌没有到期时刻（后端没给）→ 照样认为可用', async () => {
    localStorage.setItem(TOKEN_STORAGE_KEY, 'expired-token');
    localStorage.setItem(TOKEN_EXPIRES_STORAGE_KEY, String(Date.now() - 1_000));
    localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, 'rt-no-expiry');

    const { useSessionStore } = await loadSession();

    expect(useSessionStore.getState().status).toBe('loading');
    expect(useSessionStore.getState().refreshToken).toBe('rt-no-expiry');
  });

  it('令牌 + 身份快照 → 直接 authenticated（刷新不闪登录页）', async () => {
    localStorage.setItem(TOKEN_STORAGE_KEY, 'good-token');
    localStorage.setItem(TOKEN_EXPIRES_STORAGE_KEY, String(Date.now() + 60_000));
    localStorage.setItem(SUBJECT_STORAGE_KEY, JSON.stringify(subject));

    const { useSessionStore } = await loadSession();

    expect(useSessionStore.getState().status).toBe('authenticated');
    expect(useSessionStore.getState().subject).toEqual(subject);
    expect(useSessionStore.getState().token).toBe('good-token');
  });

  it('令牌存在但没有快照 → loading（等 /me 定性）', async () => {
    localStorage.setItem(TOKEN_STORAGE_KEY, 'good-token');

    const { useSessionStore } = await loadSession();

    expect(useSessionStore.getState().status).toBe('loading');
    expect(useSessionStore.getState().token).toBe('good-token');
  });

  it('快照损坏 → 当作没有，退回 loading 而不是崩溃', async () => {
    localStorage.setItem(TOKEN_STORAGE_KEY, 'good-token');
    localStorage.setItem(SUBJECT_STORAGE_KEY, '{"id": 1');

    const { useSessionStore } = await loadSession();

    expect(useSessionStore.getState().status).toBe('loading');
  });

  it('快照缺 permissions → 视为无效（避免无权限判定依据）', async () => {
    localStorage.setItem(TOKEN_STORAGE_KEY, 'good-token');
    localStorage.setItem(SUBJECT_STORAGE_KEY, JSON.stringify({ id: 'u_1', name: '老大' }));

    const { useSessionStore } = await loadSession();

    expect(useSessionStore.getState().status).toBe('loading');
  });
});

describe('session store · 写入与清空', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('signIn 同时落盘令牌、过期时间与身份', async () => {
    const { useSessionStore } = await loadSession();

    useSessionStore.getState().signIn({ token: 'tk', expiresIn: 3_600, subject });

    const state = useSessionStore.getState();
    expect(state.status).toBe('authenticated');
    expect(state.token).toBe('tk');
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBe('tk');
    expect(JSON.parse(localStorage.getItem(SUBJECT_STORAGE_KEY) ?? '{}')).toEqual(subject);

    const expiresAt = Number(localStorage.getItem(TOKEN_EXPIRES_STORAGE_KEY));
    expect(expiresAt).toBeGreaterThan(Date.now());
    // 内存态与存储必须一致：过期计时器读的是 state，不是 localStorage
    expect(state.expiresAt).toBe(expiresAt);
  });

  it('后端没给 expiresIn 时不编造到期时刻（交给 401 兜底）', async () => {
    const { useSessionStore } = await loadSession();

    useSessionStore.getState().signIn({ token: 'tk', subject });

    expect(useSessionStore.getState().expiresAt).toBeNull();
    expect(localStorage.getItem(TOKEN_EXPIRES_STORAGE_KEY)).toBeNull();
  });

  it('setSubject 会更新快照（改昵称后刷新仍是新名字）', async () => {
    const { useSessionStore } = await loadSession();
    useSessionStore.getState().signIn({ token: 'tk', expiresIn: 3_600, subject });

    useSessionStore.getState().setSubject({ ...subject, name: '新昵称' });

    expect(useSessionStore.getState().subject.name).toBe('新昵称');
    expect(JSON.parse(localStorage.getItem(SUBJECT_STORAGE_KEY) ?? '{}').name).toBe('新昵称');
  });

  it('signOut 清空内存与存储，且不留残余', async () => {
    const { useSessionStore } = await loadSession();
    useSessionStore.getState().signIn({ token: 'tk', expiresIn: 3_600, subject });

    useSessionStore.getState().signOut();

    const state = useSessionStore.getState();
    expect(state.status).toBe('anonymous');
    expect(state.token).toBeNull();
    expect(state.expiresAt).toBeNull();
    expect(state.subject.id).toBe('anonymous');
    expect(state.signOutReason?.kind).toBe('manual');
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
    expect(localStorage.getItem(SUBJECT_STORAGE_KEY)).toBeNull();
  });

  it('signIn 会清掉上一次的登出原因（重新登录后不该再说"已过期"）', async () => {
    const { useSessionStore } = await loadSession();
    useSessionStore.getState().signOut('expired');
    expect(useSessionStore.getState().signOutReason).not.toBeNull();

    useSessionStore.getState().signIn({ token: 'tk', expiresIn: 3_600, subject });

    expect(useSessionStore.getState().signOutReason).toBeNull();
  });

  it('刷新令牌与 access 令牌一起落盘、一起清空（少清一个就等于令牌没退干净）', async () => {
    const { useSessionStore } = await loadSession();

    useSessionStore.getState().signIn({
      token: 'tk',
      expiresIn: 7_200,
      refreshToken: 'rt',
      refreshExpiresIn: 604_800,
      subject,
    });

    expect(useSessionStore.getState().refreshToken).toBe('rt');
    expect(localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY)).toBe('rt');
    expect(Number(localStorage.getItem(REFRESH_EXPIRES_STORAGE_KEY))).toBeGreaterThan(Date.now());

    useSessionStore.getState().signOut();

    expect(useSessionStore.getState().refreshToken).toBeNull();
    expect(localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY)).toBeNull();
    expect(localStorage.getItem(REFRESH_EXPIRES_STORAGE_KEY)).toBeNull();
  });

  /* FE3-02 的回归用例：以前 setSubject 不看令牌，迟到的 /me 能把已退出的会话
     翻回 authenticated —— 界面复活，而且把身份快照重新写回 localStorage。 */
  it('退出之后到达的 setSubject 被丢弃：不复活界面，也不把身份写回磁盘', async () => {
    const { useSessionStore } = await loadSession();
    useSessionStore.getState().signIn({ token: 'tk', expiresIn: 3_600, subject });
    useSessionStore.getState().signOut();

    useSessionStore.getState().setSubject(subject);

    const state = useSessionStore.getState();
    expect(state.status).toBe('anonymous');
    expect(state.token).toBeNull();
    expect(state.subject.id).toBe('anonymous');
    expect(localStorage.getItem(SUBJECT_STORAGE_KEY)).toBeNull();
  });

  it('续期只换票：身份与快照不动，令牌与到期时刻一起更新', async () => {
    const { useSessionStore } = await loadSession();
    useSessionStore.getState().signIn({
      token: 'tk',
      expiresIn: 7_200,
      refreshToken: 'rt',
      refreshExpiresIn: 604_800,
      subject,
    });

    useSessionStore.getState().applyRefreshedTokens({
      token: 'tk2',
      expiresIn: 7_200,
      refreshToken: 'rt2',
      refreshExpiresIn: 604_800,
    });

    const state = useSessionStore.getState();
    expect(state.token).toBe('tk2');
    expect(state.refreshToken).toBe('rt2');
    expect(state.subject).toEqual(subject);
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBe('tk2');
    expect(localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY)).toBe('rt2');
  });

  it('续期结果迟到（期间已退出）→ 丢弃，不把会话续回来', async () => {
    const { useSessionStore } = await loadSession();
    useSessionStore.getState().signIn({ token: 'tk', expiresIn: 3_600, subject });
    useSessionStore.getState().signOut();

    useSessionStore.getState().applyRefreshedTokens({ token: 'tk2', expiresIn: 3_600 });

    expect(useSessionStore.getState().status).toBe('anonymous');
    expect(useSessionStore.getState().token).toBeNull();
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
  });

  /* 冷启动换票：本来没有 access，只有刷新令牌。
     票要收下，但身份还没到手（/me 还没回），状态必须留在 loading ——
     置 authenticated 会让导航与 <Can> 带着空 subject 渲染一帧。 */
  it('冷启动换票（没有 access）→ 收下新票，仍停在 loading 等 /me', async () => {
    const { useSessionStore } = await loadSession();
    useSessionStore.setState({
      status: 'loading',
      token: null,
      expiresAt: null,
      refreshToken: 'rt',
      subject: { id: 'anonymous', name: '匿名', roles: [], permissions: [] },
    });

    useSessionStore.getState().applyRefreshedTokens({
      token: 'tk2',
      expiresIn: 7_200,
      refreshToken: 'rt2',
      refreshExpiresIn: 604_800,
    });

    const state = useSessionStore.getState();
    expect(state.status).toBe('loading');
    expect(state.token).toBe('tk2');
    expect(state.refreshToken).toBe('rt2');
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBe('tk2');
    expect(localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY)).toBe('rt2');
  });

  it('既没有 access 也没有刷新令牌 → 迟到的换票结果被丢弃', async () => {
    const { useSessionStore } = await loadSession();
    useSessionStore.setState({ status: 'anonymous', token: null, refreshToken: null });

    useSessionStore.getState().applyRefreshedTokens({ token: 'tk2', expiresIn: 7_200 });

    expect(useSessionStore.getState().status).toBe('anonymous');
    expect(useSessionStore.getState().token).toBeNull();
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
  });

  /* FE4-08：换号时 A 的 /me 迟到。令牌是 B 的，写进来的却是 A 的身份 ——
     界面会带着 B 的令牌显示 A 的权限。 */
  it('setSubject 拒绝写入"另一个人"（换号后迟到的 /me）', async () => {
    const { useSessionStore } = await loadSession();
    useSessionStore.getState().signIn({
      token: 'b-token',
      expiresIn: 3_600,
      subject: { ...subject, id: 'u_b', name: 'B' },
    });

    useSessionStore.getState().setSubject({ ...subject, id: 'u_a', name: 'A' });

    expect(useSessionStore.getState().subject.id).toBe('u_b');
    expect(useSessionStore.getState().subject.name).toBe('B');
  });

  it('setSubject 允许从占位身份补全（冷启动时还是 anonymous）', async () => {
    const { useSessionStore } = await loadSession();
    // 冷启动：有令牌、没有快照 → subject 是占位身份
    useSessionStore.setState({ status: 'loading', token: 'tk' });

    useSessionStore.getState().setSubject(subject);

    expect(useSessionStore.getState().status).toBe('authenticated');
    expect(useSessionStore.getState().subject.id).toBe('u_1');
  });

  it('adoptStoredTokens 跟另一个标签页换过的票（同一个人）', async () => {
    const { useSessionStore } = await loadSession();
    useSessionStore.getState().signIn({ token: 'tk', expiresIn: 3_600, subject });

    // 另一个标签页续期后写进存储的新票
    localStorage.setItem(TOKEN_STORAGE_KEY, 'tk-other');
    localStorage.setItem(TOKEN_EXPIRES_STORAGE_KEY, String(Date.now() + 7_200_000));
    localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, 'rt-other');
    localStorage.setItem(SUBJECT_STORAGE_KEY, JSON.stringify(subject));

    expect(useSessionStore.getState().adoptStoredTokens()).toBe(true);
    expect(useSessionStore.getState().token).toBe('tk-other');
    expect(useSessionStore.getState().refreshToken).toBe('rt-other');
  });

  it('adoptStoredTokens 不跟换账号的票（那会带着本页的查询缓存）', async () => {
    const { useSessionStore } = await loadSession();
    useSessionStore.getState().signIn({ token: 'tk', expiresIn: 3_600, subject });

    localStorage.setItem(TOKEN_STORAGE_KEY, 'tk-other');
    localStorage.setItem(SUBJECT_STORAGE_KEY, JSON.stringify({ ...subject, id: 'u_2' }));

    expect(useSessionStore.getState().adoptStoredTokens()).toBe(false);
    expect(useSessionStore.getState().token).toBe('tk');
  });
});
