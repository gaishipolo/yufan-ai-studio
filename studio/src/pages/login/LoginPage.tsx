import { Navigate, useLocation } from 'react-router-dom';
import { Info, Sparkles } from 'lucide-react';
import { LoginForm } from '@/features/auth/LoginForm';
import { useSubject } from '@/shared/access/use-access';
import { useSessionStatus } from '@/shared/access/use-session';
import { firstAccessiblePath } from '@/shared/registry';
import { useRegistry } from '@/shared/registry/use-registry';
import type { LoginRedirectState } from '@/shared/access/RequireAuth';
import type { SignOutReason } from '@/shared/access/session';
import {
  APP_BRAND,
  APP_BRAND_EN,
  APP_NAME,
  APP_VERSION,
  LOGIN_PATH,
} from '@/shared/config/app';
import { FullPageLoader } from '@/shared/ui/FullPageLoader';
import { BrandMark, Notice, Toaster } from '@/shared/ui';

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
 * 1. 未登录直接打开 → 显示表单，登录后进「该用户能打开的第一个页面」；
 * 2. 被守卫从深链接拦过来 → 登录后回到原地址（`location.state.from`）；
 * 3. 已登录又访问 /login → 直接放行，不要出现「已登录还能看见登录页」。
 *
 * 另外 `location.state.reason` 会带来「会话为什么结束」，用于顶部提示。
 */
export default function LoginPage() {
  const status = useSessionStatus();
  const subject = useSubject();
  const registry = useRegistry();
  const location = useLocation();
  const state = location.state as LoginRedirectState | null;
  const from = state?.from;
  const notice = state?.reason ? SESSION_ENDED_NOTICE[state.reason] : undefined;

  if (status === 'loading') return <FullPageLoader label="正在恢复登录状态…" />;

  if (status === 'authenticated') {
    // 深链接被守卫拦过来时原路返回；否则落到该用户能打开的第一个页面。
    // 判据与侧栏同源（firstAccessiblePath 走 navGroups + can），
    // 不会把只有「生图」权限的人丢进无权访问的任务中心。
    const target = from && from !== LOGIN_PATH ? from : firstAccessiblePath(subject, registry);
    return <Navigate to={target} replace />;
  }

  return (
    <div className="grid min-h-screen md:grid-cols-[1.05fr_minmax(0,1fr)]">
      {/* 品牌区：移动端隐藏，避免把小屏的登录表单挤出首屏 */}
      {/* 品牌区：墨底纸字。深青渐变是上一代的配色，
          跟整套纸墨语言打架 —— 换成一块墨，像报纸的封面版。 */}
      <aside className="relative hidden flex-col justify-between overflow-hidden bg-[#17150f] p-10 text-[#f2eee4] md:flex">
        {/* 胶片齿孔边：和顶栏那道是同一个签名，只是反色。
            登录页是这套视觉语言的第一次亮相 —— 签名要在这里也出现，
            否则品牌面板和内页像两个产品。 */}
        <div className="film-edge film-edge-dark absolute inset-x-0 top-0" aria-hidden />

        <div
          className="pointer-events-none absolute -right-24 -top-24 h-72 w-72 rounded-full bg-[radial-gradient(circle,rgba(176,57,28,.20),transparent_65%)]"
          aria-hidden
        />

        <div className="relative flex items-center gap-2.5">
          <BrandMark size={32} />
          <div className="flex flex-col leading-none">
            <b className="font-serif text-lead font-bold tracking-[3px] text-[#f2eee4]">{APP_BRAND}</b>
            <span className="mt-1 font-mono text-[7.5px] tracking-[1.8px] text-[#f2eee4]/45">{APP_BRAND_EN}</span>
          </div>
          <div className="h-4 w-px bg-[#f2eee4]/25" />
          <span className="text-sm font-semibold tracking-[.3px] text-[#f2eee4]/85">{APP_NAME}</span>
        </div>

        <div className="relative max-w-[420px]">
          {/* 大字报式标题：38px、紧字距。中文标题在这么大的字号下
              不适合加粗，600 足够压住画面，又留得住笔画。 */}
          <h1 className="font-serif text-[38px] font-semibold leading-[1.16] tracking-[-.6px]">
            从一句创意
            <br />
            到可直接投放的短视频
          </h1>
          <p className="mt-4 text-body leading-[1.75] text-[#f2eee4]/70">
            分镜、生成、素材与审核在同一条流水线上完成，
            团队按角色分工，权限精确到按钮。
          </p>

          {/* 卖点之间用发丝线分开，不靠间距堆 —— 深色底上的一条线
              比一层淡背景更安静，也更像编辑部排版而不是 PPT。 */}
          <ul className="mt-7 border-t border-[#f2eee4]/15">
            {['全链路任务追踪，失败自动重试', '模型与时长按项目统一配置', '账号、角色、权限点三件套'].map(
              (item) => (
                <li
                  key={item}
                  className="flex items-center gap-2 border-b border-[#f2eee4]/15 py-2.5 text-body text-[#f2eee4]/85"
                >
                  {/* 朱红圆点：编辑部的红笔标记 —— 只在「要读的那几行」上出现 */}
                  <Sparkles size={12} className="shrink-0 text-[#b0391c]" aria-hidden />
                  {item}
                </li>
              ),
            )}
          </ul>
        </div>

        <p className="relative font-mono text-meta tracking-[.08em] text-[#f2eee4]/45">
          © 2026 {APP_BRAND} {APP_BRAND_EN} · {APP_VERSION}
        </p>
      </aside>

      <main className="flex flex-col items-center justify-center px-6 py-12">
        <div className="w-full max-w-[336px]">
          <div className="mb-6 flex items-center gap-2 md:hidden">
            <BrandMark size={26} />
            <b className="shrink-0 whitespace-nowrap font-serif text-lead font-bold tracking-[3px] text-ink">
              {APP_BRAND}
            </b>
            <span className="whitespace-nowrap text-body font-semibold text-ink">{APP_NAME}</span>
          </div>

          <h2 className="text-title font-bold tracking-[.3px] text-ink">登录</h2>
          {/* 「使用工作台账号登录」是废话（这里只有一种登录方式）；
              「登录即表示同意内部使用规范」全站找不到那个规范页面 ——
              指着一个不存在的地方不算解释。真正有用的只有「忘记密码找谁」。 */}
          <p className="mt-1.5 text-meta leading-[1.6] text-ink-mute">
            忘记密码请联系管理员重置。
          </p>

          {notice && (
            <Notice tone="warning" className="mt-3">
              <span className="flex items-start gap-1.5">
                <Info size={12} className="mt-[2px] shrink-0" aria-hidden />
                {notice}
              </span>
            </Notice>
          )}

          <div className="mt-4 rounded-panel border border-line-soft bg-panel p-4">
            {/* 跳转由上面的 authenticated 分支完成：会话一变就重定向，
                在表单里再 navigate 一次只会多一条历史记录 */}
            <LoginForm />
          </div>
        </div>
      </main>

      <Toaster />
    </div>
  );
}
