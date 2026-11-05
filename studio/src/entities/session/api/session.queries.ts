import { useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSessionStore } from '@/shared/access/session';
import { shouldRetryQuery } from '@/shared/api/retry';
import { toSubject } from '../model/types';
import { sessionApi } from './session.api';
import type { LoginPayload, PasswordChangePayload, ProfilePatch } from '../model/types';

export const sessionKeys = {
  all: ['identity'] as const,
  me: ['identity', 'me'] as const,
};

/**
 * 当前身份。
 *
 * - `enabled` 由调用方控制（有令牌才请求），因此匿名访问不会打 401 日志。
 * - 成功后自动回写 session store：顶栏、导航、<Can> 全部跟着刷新，
 *   这是「刷新页面后权限仍正确」的唯一链路，别在别处再写一份 setSubject。
 * - `retry` 只重试网络级失败与 5xx：401 就是 401，重试只会让登录页晚 3 秒出现；
 *   而一次瞬时 502 值得再试一遍，否则引导会直接掉进「恢复失败」页。
 */
export function useMe(enabled: boolean) {
  const setSubject = useSessionStore((state) => state.setSubject);

  const query = useQuery({
    queryKey: sessionKeys.me,
    queryFn: sessionApi.me,
    enabled,
    retry: shouldRetryQuery,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    if (query.data) setSubject(toSubject(query.data));
  }, [query.data, setSubject]);

  return query;
}

/**
 * 登录。
 * 写会话的动作放在这里而不是页面里：否则每个调用点都要记得「先 setQueryData 再 signIn」，
 * 漏一步就会出现「顶栏有名字但接口没带令牌」这类难查的状态。
 */
export function useLogin() {
  const queryClient = useQueryClient();
  const signIn = useSessionStore((state) => state.signIn);

  return useMutation({
    mutationFn: (payload: LoginPayload) => sessionApi.login(payload),
    onSuccess: (result) => {
      // 登录接口已返回完整 Me，先塞进缓存，避免登录后立刻再请求一次 /me
      queryClient.setQueryData(sessionKeys.me, result.user);
      signIn({
        token: result.accessToken,
        expiresIn: result.expiresIn,
        // 少了这一对，2 小时后就只能清场回登录页（后端给了 7 天的会话，别浪费）
        refreshToken: result.refreshToken,
        refreshExpiresIn: result.refreshExpiresIn,
        subject: toSubject(result.user),
      });
    },
  });
}

/** 改自己的资料：后端回整份 Me，直接覆盖缓存与全局身份（顶栏昵称立即变） */
export function useUpdateProfile() {
  const queryClient = useQueryClient();
  const setSubject = useSessionStore((state) => state.setSubject);

  return useMutation({
    mutationFn: (patch: ProfilePatch) => sessionApi.updateProfile(patch),
    onSuccess: (me) => {
      queryClient.setQueryData(sessionKeys.me, me);
      setSubject(toSubject(me));
    },
  });
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (payload: PasswordChangePayload) => sessionApi.changePassword(payload),
  });
}
