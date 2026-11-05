import { useState } from 'react';
import { KeyRound, UserRound } from 'lucide-react';
import { ChangePasswordModal } from './ChangePasswordModal';
import { ProfileModal } from './ProfileModal';

const ITEM_CLASS =
  'flex w-full items-center gap-2 rounded-lg px-2 py-[7px] text-left text-xs text-ink-soft transition-colors hover:bg-panel-soft hover:text-ink';

/**
 * 顶栏账号菜单的内容（通过 `user-menu.items` 插槽注入）。
 * 弹窗状态放在这里而不是 UserMenu：骨架不需要知道有哪些业务弹窗。
 */
export function UserMenuItems() {
  const [dialog, setDialog] = useState<'profile' | 'password' | null>(null);

  return (
    <>
      <button type="button" className={ITEM_CLASS} onClick={() => setDialog('profile')}>
        <UserRound size={14} aria-hidden />
        个人资料
      </button>
      <button type="button" className={ITEM_CLASS} onClick={() => setDialog('password')}>
        <KeyRound size={14} aria-hidden />
        修改密码
      </button>

      <ProfileModal open={dialog === 'profile'} onClose={() => setDialog(null)} />
      <ChangePasswordModal open={dialog === 'password'} onClose={() => setDialog(null)} />
    </>
  );
}
