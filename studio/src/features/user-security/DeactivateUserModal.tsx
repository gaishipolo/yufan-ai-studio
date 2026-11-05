import { useEffect, useState } from 'react';
import { Button, Modal } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { useDeactivateUser } from '@/entities/user/api/user.queries';
import type { User } from '@/entities/user/model/types';

/**
 * 停用用户（后端 `DELETE /users/{id}`，保留历史归属，不是物理删除）。
 * 后端会拒绝「停用自己」「停用最后一个超管」，错误信息直接展示。
 */
export function DeactivateUserModal({
  open,
  user,
  onClose,
}: {
  open: boolean;
  user: User | null;
  onClose: () => void;
}) {
  const deactivate = useDeactivateUser();
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    if (open) setFormError(null);
  }, [open]);

  const handleConfirm = () => {
    if (!user) return;
    setFormError(null);
    deactivate.mutate(user.id, {
      onSuccess: () => {
        toast('用户已停用', 'success');
        onClose();
      },
      onError: (error) => setFormError(error.message),
    });
  };

  return (
    <Modal open={open} onClose={onClose} title="停用用户" width={440}>
      <div className="space-y-3 px-[18px] py-4">
        <p className="text-xs leading-[1.7] text-ink-soft">
          确定停用
          <span className="mx-1 font-semibold text-ink">{user?.username}</span>
          吗？停用后该账号无法登录、已签发的令牌立即失效，历史任务与素材归属保留。
        </p>
        <p className="text-[11.5px] leading-[1.6] text-ink-mute">
          之后可在列表里重新启用；停用自己或停用最后一个超管会被后端拒绝。
        </p>

        {formError && (
          <p className="rounded-lg border border-[#fad7d4] bg-[#feeceb] px-2.5 py-2 text-[11.5px] text-[#d94036]" role="alert">
            {formError}
          </p>
        )}

        <div className="flex items-center justify-end gap-1.5 pt-1">
          <Button onClick={onClose} disabled={deactivate.isPending}>
            取消
          </Button>
          <Button variant="danger" onClick={handleConfirm} loading={deactivate.isPending}>
            确认停用
          </Button>
        </div>
      </div>
    </Modal>
  );
}
