import { useState } from 'react';
import { cn } from '@/shared/lib/cn';
import { initialsOf } from '../model/types';

/** 只要这三个字段就能画头像：列表行、顶栏、表单预览传的都不是完整 User */
export interface AvatarUser {
  displayName?: string;
  username?: string;
  avatarUrl?: string | null;
}

export interface UserAvatarProps {
  user: AvatarUser;
  /** 像素边长 */
  size?: number;
  /** circle = 用户列表的圆形；square = 顶栏/表单的圆角方形 */
  shape?: 'circle' | 'square';
  /** soft = 蓝底；muted = 停用账号；accent = 顶栏渐变 */
  tone?: 'soft' | 'muted' | 'accent';
  className?: string;
}

const TONES = {
  soft: 'bg-primary-soft text-primary-deep',
  muted: 'bg-line-soft text-ink-mute',
  accent: 'bg-[linear-gradient(140deg,#4f8cff,#12c4c8)] text-white shadow-[0_4px_10px_rgba(43,124,255,.3)]',
} as const;

/**
 * 统一头像：有 `avatarUrl` 就显示图片，加载失败**无声退回首字母**。
 *
 * 头像地址是用户自己填的，链接失效是常态（图床挂了、仓库转私有）。
 * 不做兜底的话，一个 404 会让整行出现浏览器默认的碎图图标——比没有头像更难解释。
 *
 * 失败状态按 URL 记忆而不是布尔量：换了新地址自动重试，不用等组件卸载。
 */
export function UserAvatar({
  user,
  size = 28,
  shape = 'circle',
  tone = 'soft',
  className,
}: UserAvatarProps) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);

  const url = user.avatarUrl?.trim() || null;
  const showImage = url !== null && failedUrl !== url;
  const radius = shape === 'circle' ? 'rounded-full' : 'rounded-[9px]';

  if (showImage) {
    return (
      <img
        src={url}
        alt=""
        aria-hidden
        width={size}
        height={size}
        onError={() => setFailedUrl(url)}
        className={cn('shrink-0 object-cover', radius, className)}
        style={{ width: size, height: size }}
      />
    );
  }

  return (
    <span
      aria-hidden
      className={cn('grid shrink-0 place-items-center font-semibold', radius, TONES[tone], className)}
      style={{ width: size, height: size, fontSize: Math.max(10, Math.round(size * 0.4)) }}
    >
      {initialsOf(user)}
    </span>
  );
}
