import { useEffect, useState, type FormEvent } from 'react';
import { Button, FormField, Modal, TextInput } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { useResetPassword } from '@/entities/user/api/user.queries';
import { PASSWORD_MIN_LENGTH, checkPassword } from '@/entities/user/model/validation';
import type { User } from '@/entities/user/model/types';

/** 管理员重置密码：设置一个新的临时口令，由管理员线下告知本人 */
export function ResetPasswordModal({
  open,
  user,
  onClose,
}: {
  open: boolean;
  user: User | null;
  onClose: () => void;
}) {
  const resetPassword = useResetPassword();
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setPassword('');
    setConfirm('');
    setFormError(null);
  }, [open]);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!user) return;

    const passwordError = checkPassword(password, '新密码');
    if (passwordError) {
      setFormError(passwordError);
      return;
    }
    if (password !== confirm) {
      setFormError('两次输入不一致');
      return;
    }

    resetPassword.mutate(
      { id: user.id, newPassword: password },
      {
        onSuccess: () => {
          toast('密码已重置，请线下告知本人', 'success');
          onClose();
        },
        onError: (error) => setFormError(error.message),
      },
    );
  };

  return (
    <Modal open={open} onClose={onClose} title={`重置密码 · ${user?.username ?? ''}`} width={440}>
      <form className="space-y-3 px-[18px] py-4" onSubmit={handleSubmit}>
        {/* 后端 `reset_password` 里 `set_password` 之后紧跟
            `sessions.revoke_all(reason="password_reset")`：重置 = 强制下线所有设备。
            这句话以前写的是"会话不受影响"，正好把这次操作最有价值的安全属性
            说成了"没有"，也让管理员以为对方还能继续用。 */}
        <p className="text-[11.5px] leading-[1.6] text-ink-mute">
          重置后旧密码立即失效，<strong className="font-semibold text-ink">该账号所有已登录设备会被强制下线</strong>
          （含正在使用中的会话），本人需用新口令重新登录。
        </p>

        <FormField
          label="新密码"
          htmlFor="reset-password"
          required
          hint={`至少 ${PASSWORD_MIN_LENGTH} 位，不能是纯数字`}
        >
          <TextInput
            id="reset-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </FormField>

        <FormField label="确认新密码" htmlFor="reset-password-confirm" required>
          <TextInput
            id="reset-password-confirm"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
          />
        </FormField>

        {formError && (
          <p className="rounded-lg border border-[#fad7d4] bg-[#feeceb] px-2.5 py-2 text-[11.5px] text-[#d94036]" role="alert">
            {formError}
          </p>
        )}

        <div className="flex items-center justify-end gap-1.5 pt-1">
          <Button onClick={onClose} disabled={resetPassword.isPending}>
            取消
          </Button>
          <Button variant="primary" type="submit" loading={resetPassword.isPending}>
            重置
          </Button>
        </div>
      </form>
    </Modal>
  );
}
