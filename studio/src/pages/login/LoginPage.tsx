import { Navigate, useLocation } from 'react-router-dom';
import { Info, Sparkles } from 'lucide-react';
import { LoginForm } from '@/features/auth/LoginForm';
import { useSessionStatus } from '@/shared/access/use-session';
import type { LoginRedirectState } from '@/shared/access/RequireAuth';
import type { SignOutReason } from '@/shared/access/session';
import { APP_NAME, HOME_PATH, LOGIN_PATH } from '@/shared/config/app';
import { FullPageLoader } from '@/shared/ui/FullPageLoader';
import { BrandMark, Toaster } from '@/shared/ui';

/**
 * 会话结束的说明文案。
 * 「主动退出」不在此列——用户自己点的按钮，不需要再解释；
 * 其余都是被迫的，得让用户知道不是自己操作错了。
 */
const SESSION_ENDED_NOTICE: Partial<Record<SignOutReason, string>> = {
  expired: '登录状态已过期，请重新登录后继续。',
  unauthorized: '登录状态已失效（可能已在别处被重置），请重新登录。',
  external: '此账号已在其他标签页退出登录。',
  // 不是故障：后端改密会吊销全部会话（安全属性），所以要说清"这是刚才那个操作的结果"
  password: '密码已修改，所有设备已退出登录，请用新密码重新登录。',
};

/**
 * 登录页（`layout: 'bare'`，不套外壳）。
 *
 * 三种进入方式都要正确处理：
 * 1. 未登录直接打开 → 显示表单，登录后进首页；
 * 2. 被守卫从深链接拦过来 → 登录后回到原地址（`location.state.from`）；
 * 3. 已登录又访问 /login → 直接放行，不要出现「已登录还能看见登录页」。
 *
 * 另外 `location.state.reason` 会带来「会话为什么结束」，用于顶部提示。
 */
export default function LoginPage() {
  const status = useSessionStatus();
  const location = useLocation();
  const state = location.state as LoginRedirectState | null;
  const from = state?.from;
  const notice = state?.reason ? SESSION_ENDED_NOTICE[state.reason] : undefined;

  if (status === 'loading') return <FullPageLoader label="正在恢复登录状态…" />;

  if (status === 'authenticated') {
    const target = from && from !== LOGIN_PATH ? from : HOME_PATH;
    return <Navigate to={target} replace />;
  }

  return (
    <div className="grid min-h-screen md:grid-cols-[1.05fr_minmax(0,1fr)]">
      {/* 品牌区：移动端隐藏，避免把小屏的登录表单挤出首屏 */}
      <aside className="relative hidden flex-col justify-between overflow-hidden bg-[linear-gradient(155deg,#0d2431_0%,#123a4a_48%,#0b5a63_100%)] p-10 text-white md:flex">
        <div
          className="pointer-events-none absolute -right-24 -top-24 h-72 w-72 rounded-full bg-[radial-gradient(circle,rgba(18,196,200,.35),transparent_65%)]"
          aria-hidden
        />

        <div className="relative flex items-center gap-2.5">
          <BrandMark size={32} />
          <div className="flex flex-col leading-none">
            <b className="text-[17px] font-extrabold tracking-[3px] text-white">雨范</b>
            <span className="mt-1 text-[7.5px] tracking-[1.8px] text-white/45">AI STUDIO</span>
          </div>
          <div className="h-4 w-px bg-white/25" />
          <span className="text-sm font-semibold tracking-[.3px] text-white/90">{APP_NAME}</span>
        </div>

        <div className="relative max-w-[420px]">
          <h1 className="text-[26px] font-bold leading-[1.35] tracking-[.5px]">
            从一句创意
            <br />
            到可直接投放的短视频
          </h1>
          <p className="mt-3 text-[12.5px] leading-[1.8] text-white/65">
            分镜、生成、素材与审核在同一条流水线上完成，
            团队按角色分工，权限精确到按钮。
          </p>

          <ul className="mt-6 space-y-2.5">
            {['全链路任务追踪，失败自动重试', '模型与时长按项目统一配置', '账号、角色、权限点三件套'].map(
              (item) => (
                <li key={item} className="flex items-center gap-2 text-[12px] text-white/75">
                  <Sparkles size={12} className="shrink-0 text-[#59e2d6]" aria-hidden />
                  {item}
                </li>
              ),
            )}
          </ul>
        </div>

        <p className="relative text-[11px] text-white/40">© 2026 雨范 AI Studio · v3.0</p>
      </aside>

      <main className="flex flex-col items-center justify-center px-6 py-12">
        <div className="w-full max-w-[336px]">
          <div className="mb-6 flex items-center gap-2 md:hidden">
            <BrandMark size={26} />
            <b className="shrink-0 whitespace-nowrap text-[17px] font-extrabold tracking-[3px] text-ink">
              雨范
            </b>
            <span className="whitespace-nowrap text-sm font-semibold text-ink">{APP_NAME}</span>
          </div>

          <h2 className="text-[19px] font-bold tracking-[.3px] text-ink">登录</h2>
          <p className="mt-1.5 text-[11.5px] leading-[1.6] text-ink-mute">
            使用工作台账号登录；忘记密码请联系管理员重置。
          </p>

          {notice && (
            <p
              role="status"
              className="mt-3 flex items-start gap-1.5 rounded-lg border border-[#fbe8c8] bg-[#fff9ee] px-2.5 py-2 text-[11.5px] leading-[1.6] text-[#8a5b00]"
            >
              <Info size={12} className="mt-[2px] shrink-0" aria-hidden />
              {notice}
            </p>
          )}

          <div className="mt-4 rounded-panel border border-line-soft bg-panel p-4 shadow-card">
            {/* 跳转由上面的 authenticated 分支完成：会话一变就重定向，
                在表单里再 navigate 一次只会多一条历史记录 */}
            <LoginForm />
          </div>

          <p className="mt-4 text-center text-[10.5px] leading-[1.6] text-ink-mute">
            登录即表示同意内部使用规范。
          </p>
        </div>
      </main>

      <Toaster />
    </div>
  );
}
