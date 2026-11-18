import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useMe, useUpdateProfile } from '@/entities/session/api/session.queries';
import { UserAvatar } from '@/entities/user/ui/UserAvatar';
import { checkAvatarUrl, checkEmail } from '@/entities/user/model/validation';
import { toast } from '@/shared/lib/toast';
import { Button, FormField, Modal, Notice, TextInput } from '@/shared/ui';

/**
 * 个人资料：只能改展示信息（昵称 / 邮箱 / 头像）。
 * 停用、角色这类管理动作刻意不放这里——那属于管理员在「用户与权限」里做的事，
 * 自助路径放开会变成「误点一下把自己锁在门外」。
 */
export function ProfileModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data: me } = useMe(open);
  const updateProfile = useUpdateProfile();

  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  const [avatarUrl, setAvatarUrl] = useState('');
  const [formError, setFormError] = useState<string | null>(null);

  /* 只在拿到 me 的那一次回填，避免后台刷新把用户正在输入的内容冲掉 */
  const filledRef = useRef(false);
  useEffect(() => {
    if (!open) {
      filledRef.current = false;
      return;
    }
    if (filledRef.current || !me) return;
    setDisplayName(me.displayName ?? '');
    setEmail(me.email ?? '');
    setAvatarUrl(me.avatarUrl ?? '');
    setFormError(null);
    filledRef.current = true;
  }, [open, me]);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    setFormError(null);

    const emailError = checkEmail(email);
    if (emailError) {
      setFormError(emailError);
      return;
    }
    const avatarError = checkAvatarUrl(avatarUrl);
    if (avatarError) {
      setFormError(avatarError);
      return;
    }

    updateProfile.mutate(
      {
        displayName: displayName.trim(),
        email: email.trim() || null,
        avatarUrl: avatarUrl.trim() || null,
      },
      {
        onSuccess: () => {
          toast('资料已更新', 'success');
          onClose();
        },
        onError: (error) => setFormError(error.message),
      },
    );
  };

  return (
    <Modal open={open} onClose={onClose} title="个人资料" width={440}>
      <form className="space-y-3 px-[18px] py-4" onSubmit={handleSubmit}>
        {/* 「登录名不可修改」挪进 `title`：输入框已经是禁用态，
            看得见；「不可修改」是原因，悬浮才需要看。 */}
        <FormField label="用户名" htmlFor="profile-username">
          <TextInput
            id="profile-username"
            value={me?.username ?? ''}
            disabled
            readOnly
            title="登录名不可修改"
          />
        </FormField>

        <FormField label="昵称" htmlFor="profile-display-name">
          {/* 「留空则显示用户名」由 placeholder 表达 —— 输入框里能直接看见
              留空之后会显示成什么，比一行小字更准。 */}
          <TextInput
            id="profile-display-name"
            value={displayName}
            maxLength={64}
            onChange={(event) => setDisplayName(event.target.value)}
            placeholder={me?.username ?? ''}
          />
        </FormField>

        <FormField label="邮箱" htmlFor="profile-email" >
          <TextInput
            id="profile-email"
            type="email"
            autoComplete="email"
            value={email}
            maxLength={128}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="name@example.com"
          />
        </FormField>

        {/* 头像旁边就有一张实时预览图：留空时预览画的就是昵称首字，
            不用文字再说一遍。 */}
        <FormField label="头像地址" htmlFor="profile-avatar">
          <div className="flex items-center gap-2">
            <UserAvatar user={{ displayName, username: me?.username, avatarUrl }} size={32} shape="square" />
            <TextInput
              id="profile-avatar"
              className="flex-1"
              value={avatarUrl}
              maxLength={512}
              placeholder="https://…"
              onChange={(event) => setAvatarUrl(event.target.value)}
            />
          </div>
        </FormField>

        {formError && <Notice tone="error">{formError}</Notice>}

        <div className="flex items-center justify-end gap-1.5 pt-1">
          <Button onClick={onClose} disabled={updateProfile.isPending}>
            取消
          </Button>
          <Button variant="primary" type="submit" loading={updateProfile.isPending}>
            保存
          </Button>
        </div>
      </form>
    </Modal>
  );
}
