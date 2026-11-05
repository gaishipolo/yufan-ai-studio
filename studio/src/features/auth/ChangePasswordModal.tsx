import { useEffect, useState, type FormEvent } from 'react';
import { useChangePassword } from '@/entities/session/api/session.queries';
import { PASSWORD_MIN_LENGTH, checkPassword } from '@/entities/user/model/validation';
import { endSession } from '@/shared/access/end-session';
import { Button, FormField, Modal, TextInput } from '@/shared/ui';

/**
 * 修改自己的密码。
 *
 * 前端只挡「一眼能看出不合法」的输入（长度、纯数字、两次不一致），
 * 强度与「不能与旧密码相同」仍以后端为准——两边的判定终归会有偏差，
 * 前端多拦一条只是少一次往返，不能替代服务端校验。
 *
 * **成功后必定退出登录**，而且不是前端选的：后端 `change_password` 在改密之后
 * 紧跟 `revoke_all(reason="password_change")`（R10-03），当前这台也在吊销名单里
 * —— 它的 refresh 同样过不了 `iat < password_changed_at`。
 * 以前这里写的是"后端改密不吊销已签发令牌，强制重登只会制造麻烦"，与事实相反：
 * 不主动结束的话，用户会先看到一个成功提示，几百毫秒后随手一个请求才被踢下线，
 * 登录页还告诉他"登录状态已失效（可能已在别处被重置）"——听起来像被人入侵了。
 */
export function ChangePasswordModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const changePassword = useChangePassword();

  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setOldPassword('');
    setNewPassword('');
    setConfirm('');
    setFormError(null);
  }, [open]);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    setFormError(null);

    if (!oldPassword) {
      setFormError('请输入当前密码');
      return;
    }
    const passwordError = checkPassword(newPassword, '新密码');
    if (passwordError) {
      setFormError(passwordError);
      return;
    }
    if (newPassword === oldPassword) {
      setFormError('新密码不能与当前密码相同');
      return;
    }
    if (newPassword !== confirm) {
      setFormError('两次输入的新密码不一致');
      return;
    }

    changePassword.mutate(
      { oldPassword, newPassword },
      {
        onSuccess: () => {
          // 先关弹窗再清场：清场会让 <RequireAuth> 立刻重定向，留着开着的弹窗只会闪一下
          onClose();
          // 会话已经被服务端吊销，本地必须跟上。reason 用 'password' 而不是 'manual'：
          // 登录页要能说清"这是你刚改密码的结果"，而不是让用户以为被踢了
          endSession('password');
        },
        onError: (error) => setFormError(error.message),
      },
    );
  };

  return (
    <Modal open={open} onClose={onClose} title="修改密码" width={440}>
      <form className="space-y-3 px-[18px] py-4" onSubmit={handleSubmit}>
        <FormField label="当前密码" htmlFor="change-old-password" required>
          <TextInput
            id="change-old-password"
            type="password"
            autoComplete="current-password"
            value={oldPassword}
            onChange={(event) => setOldPassword(event.target.value)}
          />
        </FormField>

        <FormField
          label="新密码"
          htmlFor="change-new-password"
          required
          hint={`至少 ${PASSWORD_MIN_LENGTH} 位，不能是纯数字，且不能与当前密码相同`}
        >
          <TextInput
            id="change-new-password"
            type="password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
          />
        </FormField>

        <FormField label="确认新密码" htmlFor="change-confirm-password" required>
          <TextInput
            id="change-confirm-password"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
          />
        </FormField>

        {formError && (
          <p
            className="rounded-lg border border-danger/25 bg-danger/5 px-2.5 py-2 text-[11.5px] text-danger"
            role="alert"
          >
            {formError}
          </p>
        )}

        <div className="flex items-center justify-end gap-1.5 pt-1">
          <Button onClick={onClose} disabled={changePassword.isPending}>
            取消
          </Button>
          <Button variant="primary" type="submit" loading={changePassword.isPending}>
            更新密码
          </Button>
        </div>
      </form>
    </Modal>
  );
}
