import { useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { LOGIN_PATH } from '@/shared/config/app';
import { endSession } from '@/shared/access/end-session';

/**
 * 登出：结束会话 → 回登录页。
 *
 * `endSession` 内部做完三件事：清会话、清查询缓存、通知服务端作废会话
 * （`POST /identity/logout`，只有手动退出才调）。这里不再重复写 `queryClient.clear()`：
 * 过期、401、他页退出现在走的是同一个函数，手动退出只是多一个「立刻跳转」
 * （其余三条由 <RequireAuth> 重定向）。
 *
 * 服务端那次调用是 fire-and-forget：网络断了也要能退出去，
 * 否则用户会被困在一个他已经不想待的页面里。
 */
export function useSignOut() {
  const navigate = useNavigate();

  return useCallback(() => {
    endSession('manual');
    navigate(LOGIN_PATH, { replace: true });
  }, [navigate]);
}
