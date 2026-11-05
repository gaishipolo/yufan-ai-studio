import { useEffect, useState, type FormEvent } from 'react';
import { Lock } from 'lucide-react';
import { useLogin } from '@/entities/session/api/session.queries';
import { Button, FormField, TextInput } from '@/shared/ui';
import { describeLoginFailure, formatCooldown, type LoginFailure } from './login-feedback';

/** mock 模式下登录页给出可用账号，省掉「去 seed.py 里翻密码」这一步 */
const DEMO_HINT = 'admin / admin@12345';
const SHOW_DEMO_HINT = import.meta.env.DEV && import.meta.env.VITE_USE_MOCK === 'true';

/**
 * 登录表单。
 * 只负责「收集 + 提交 + 报错」，会话写入由 useLogin 完成；
 * 跳转通常由宿主页面监听会话状态完成，`onSuccess` 只用于确需额外动作的场景。
 *
 * 被限流（429）后按钮进入倒计时锁定：后端此时对同一「用户名 + IP」的尝试
 * 会先付等待成本再验密，前端若还能无限点，用户只会得到越来越长的转圈。
 */
export function LoginForm({ onSuccess }: { onSuccess?: () => void }) {
  const login = useLogin();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [failure, setFailure] = useState<LoginFailure | null>(null);
  const [failures, setFailures] = useState(0);
  /** 锁定截止时刻；0 表示未锁定 */
  const [lockUntil, setLockUntil] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  // 到点自动解锁。锁定期间每 0.5s 刷新一次 now 以驱动倒计时文案；
  // 依赖只有 lockUntil，定时器因此不会因为每次 tick 重建。
  useEffect(() => {
    if (!lockUntil) return;
    const timer = setInterval(() => {
      if (Date.now() >= lockUntil) {
        setLockUntil(0);
        return;
      }
      setNow(Date.now());
    }, 500);
    return () => clearInterval(timer);
  }, [lockUntil]);

  const remaining = lockUntil > now ? Math.ceil((lockUntil - now) / 1000) : 0;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (remaining > 0) return;

    if (!username.trim() || !password) {
      setFailure({ title: '请补充登录信息', detail: '用户名和密码都需要填写', cooldownSec: 0 });
      return;
    }

    setFailure(null);

    try {
      await login.mutateAsync({ username: username.trim(), password });
      setFailures(0);
      onSuccess?.();
    } catch (err) {
      const nextFailures = failures + 1;
      const described = describeLoginFailure(err, nextFailures);
      setFailures(nextFailures);
      setFailure(described);
      if (described.cooldownSec > 0) {
        setNow(Date.now());
        setLockUntil(Date.now() + described.cooldownSec * 1000);
      }
    }
  }

  const locked = remaining > 0;

  return (
    <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-3.5">
      <FormField label="用户名" htmlFor="login-username" required>
        <TextInput
          id="login-username"
          name="username"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          autoComplete="username"
          placeholder="请输入用户名"
          className="h-10 text-[13px]"
          aria-invalid={Boolean(failure)}
        />
      </FormField>

      <FormField label="密码" htmlFor="login-password" required>
        <TextInput
          id="login-password"
          name="password"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="current-password"
          placeholder="请输入密码"
          className="h-10 text-[13px]"
          aria-invalid={Boolean(failure)}
        />
      </FormField>

      {failure && (
        <div
          role="alert"
          className="rounded-lg border border-danger/25 bg-danger/5 px-2.5 py-2 text-danger"
        >
          <p className="flex items-center gap-1.5 text-[12px] font-semibold leading-[1.5]">
            {locked && <Lock size={11} aria-hidden />}
            {failure.title}
          </p>
          <p className="mt-0.5 text-[11.5px] leading-[1.6] text-danger/85">{failure.detail}</p>
        </div>
      )}

      {/* 文案说「最多」是有意的：后端是滑动窗口，解锁时刻由**第一次**失败决定，
          而前端只能从「现在」往后数 15 分钟（后端不会告诉我们剩余秒数）。
          所以真实的解锁时间只会比这个倒计时更早，写死「请 X 后再试」会变成一句
          不准确的承诺——用户提前解锁反而以为按钮坏了。 */}
      <Button
        type="submit"
        variant="primary"
        size="lg"
        block
        loading={login.isPending}
        disabled={locked}
      >
        {locked ? `最多再等 ${formatCooldown(remaining)}` : '登录'}
      </Button>

      {SHOW_DEMO_HINT && (
        <p className="text-center text-[11px] text-ink-mute">
          演示账号（mock 模式）：<span className="tabular-nums">{DEMO_HINT}</span>
        </p>
      )}
    </form>
  );
}
