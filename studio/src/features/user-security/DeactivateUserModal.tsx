import { useEffect, useState } from 'react';
import { Button, Modal, Notice } from '@/shared/ui';
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
        {/* 「之后可在列表里重新启用」是「停用」这个词自带的语义；
            留「会被后端拒绝」那半句 —— 那是猜不到的边界。 */}
        <p className="text-meta leading-[1.6] text-ink-mute">
          停用自己或停用最后一个超管会被后端拒绝。
        </p>

        {formError && (
          <Notice tone="error">{formError}</Notice>
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
