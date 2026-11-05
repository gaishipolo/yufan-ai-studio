import { useEffect, useId, useRef, useState } from 'react';
import { ChevronDown, LogOut } from 'lucide-react';
import { useMe } from '@/entities/session/api/session.queries';
import { UserAvatar } from '@/entities/user/ui/UserAvatar';
import { useSubject } from '@/shared/access/use-access';
import { useSessionToken } from '@/shared/access/use-session';
import { cn } from '@/shared/lib/cn';
import { Slot } from '@/shared/registry/Slot';
import { useSignOut } from './use-sign-out';

/**
 * 顶栏账号菜单。骨架组件：容器、身份块、退出登录归骨架；
 * 菜单项（个人资料 / 修改密码 / 偏好设置…）由模块通过 `user-menu.items` 插槽注入，
 * 因此加一项设置入口不需要改这里。
 *
 * 下拉刻意不因「点击菜单项」而关闭：插槽项通常是打开弹窗，
 * 关掉下拉会让弹窗的状态跟着组件卸载一起丢。
 */
export function UserMenu() {
  const subject = useSubject();
  const token = useSessionToken();
  const { data: me } = useMe(Boolean(token));
  const signOut = useSignOut();

  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return undefined;

    /* 弹窗是 portal 到 body 的，不在 rootRef 内：不排除掉的话，
       点「个人资料」弹出窗口会立刻被判定为「点到外面」，下拉关闭连带弹窗卸载 */
    const insideDialog = (target: EventTarget | null) =>
      Boolean((target as Element | null)?.closest?.('[role="dialog"]'));

    const onPointerDown = (event: MouseEvent) => {
      if (insideDialog(event.target)) return;
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      // Esc 先交给弹窗处理，不要让一次按键同时关掉两层
      if (event.key !== 'Escape' || insideDialog(event.target)) return;
      setOpen(false);
      buttonRef.current?.focus();
    };

    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={`账号菜单，当前账号 ${subject.name}`}
        className="flex items-center gap-1 rounded-[11px] p-0.5 pr-1 transition-colors hover:bg-[#f1f6f7]"
      >
        <UserAvatar
          user={me ?? { displayName: subject.name }}
          size={30}
          shape="square"
          tone="accent"
          className="font-bold"
        />
        <ChevronDown
          size={12}
          aria-hidden
          className={cn('text-ink-mute transition-transform duration-150', open && 'rotate-180')}
        />
      </button>

      {open && (
        <div
          id={menuId}
          className="absolute right-0 top-[calc(100%+8px)] z-50 w-[228px] overflow-hidden rounded-[12px] border border-line-soft bg-panel shadow-pop animate-popIn"
        >
          <div className="border-b border-line-soft px-3 py-2.5">
            <div className="truncate text-xs font-semibold text-ink" title={subject.name}>
              {subject.name}
            </div>
            <div className="mt-0.5 truncate text-[11px] text-ink-mute">
              {me?.username ? `@${me.username}` : '已登录'}
              {me?.roles?.length
                ? ` · ${me.roles.map((role) => role.name).join(' / ')}`
                : ''}
            </div>
          </div>

          {/* 模块注入的菜单项：个人资料、修改密码等 */}
          <div className="p-1">
            <Slot name="user-menu.items" />
          </div>

          <div className="border-t border-line-soft p-1">
            <button
              type="button"
              onClick={signOut}
              className="flex w-full items-center gap-2 rounded-lg px-2 py-[7px] text-left text-xs text-ink-soft transition-colors hover:bg-[#fdeceb] hover:text-danger"
            >
              <LogOut size={14} aria-hidden />
              退出登录
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
