import { requestLogout } from '@/shared/api/auth';
import { queryClient } from '@/shared/api/query-client';
import { useSessionStore, type SignOutReason } from './session';

/* ------------------------------------------------------------------
 * 结束会话 —— 唯一的出口
 *
 * 会话有**五条**结束路径，分别落在五个文件里：
 *   manual        用户点退出（widgets/user-menu/use-sign-out）
 *   expired       令牌到点且续期失败（use-session-watch）、冷启动换票被拒（SessionBootstrap）
 *   unauthorized  任意接口 401 且续期失败（shared/api/http 的拦截器）
 *   external      另一个标签页退出（use-session-watch 的 storage 监听）
 *   password      本人刚改过密码（features/auth/ChangePasswordModal）——
 *                 后端 `revoke_all` 已经把会话吊销，前端只是把本地状态跟上
 *
 * 只有第一条会经过 React 组件，另外三条都是**被动发生**的：用户不会在那时
 * 想到「该有人清缓存了」。以前清缓存的 `queryClient.clear()` 只写在手动退出里，
 * 于是「401 → 回登录页 → 同标签页换账号登录」会在 staleTime 内把上一个账号的
 * 生图历史、角色列表当成新账号的数据渲染出来——用户会当成数据串了报过来。
 *
 * 所以「结束会话」本身必须是原子的：清会话状态 + 清查询缓存，一处写完，
 * 五个入口都调它。清缓存是幂等的，多调一次无害，不存在顺序陷阱。
 *
 * 跳转仍然不在**这里**做：路由守卫读 status 自己会重定向，
 * 拦截器不碰路由（否则拦截器与路由会互相打架）。
 *
 * ------------------------------------------------------------------
 * 为什么只有 manual 会去通知服务端
 *
 * 后端有 `POST /identity/logout`（作废当前会话，幂等），但**不是**每条路径都该调：
 *   - manual：用户明确说「让我下线」，服务端那条会话必须真的作废。不调的话，
 *     令牌在服务端仍然有效到 2 小时后，`/me/sessions` 里那条还是「本机在线」——
 *     共用电脑上点「退出登录」等于什么也没做。这是本函数里唯一有安全含义的分支。
 *   - expired / unauthorized：会话已经不可用（续期都失败了），没有可吊销的东西。
 *   - external：另一个标签页退出时已经调过。
 *   - password：服务端改密时已经 `revoke_all`，再调一次只是多一条失败日志。
 *
 * 顺序是**先清本地、再发请求**，而且请求不阻塞、失败不报错：
 * 反过来会出现「网络断了就退不出去」，用户被困在一个已经不想待的页面里。
 * 请求要用清场**之前**取下的令牌——store 清空后拦截器注不进鉴权头，
 * 服务端就不知道该吊销哪一条会话（见 `shared/api/auth` 的旁路通道）。
 * ------------------------------------------------------------------ */

export function endSession(reason: SignOutReason = 'manual') {
  const { token } = useSessionStore.getState();

  useSessionStore.getState().signOut(reason);
  queryClient.clear();

  if (reason === 'manual' && token) {
    // fire-and-forget：退出体验优先于「确认服务端已作废」，失败也只是提前过期
    void requestLogout(token).catch(() => undefined);
  }
}
