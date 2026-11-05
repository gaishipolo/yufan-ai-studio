import { useEffect, useState, type FormEvent } from 'react';
import { TriangleAlert } from 'lucide-react';
import { Button, Modal } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { useSubject } from '@/shared/access/use-access';
import { RoleCheckboxList } from '@/entities/role/ui/RoleCheckboxList';
import type { Role } from '@/entities/role/model/types';
import { useSetUserRoles } from '@/entities/user/api/user.queries';
import type { User } from '@/entities/user/model/types';

/**
 * 分配角色：整组替换语义（后端 `PUT /users/{id}/roles`）。
 * 这是提权动作，入口本身由 `identity:role:manage` 控制，弹窗内再提示一次影响面。
 *
 * 改**自己**的角色不拦（转交超管是正常流程，先撤自己才安全），
 * 但必须提示代价：撤销后当前页面还留着旧权限快照，刷新才会掉出去。
 */
export function AssignRolesModal({
  open,
  user,
  roles,
  onClose,
}: {
  open: boolean;
  user: User | null;
  roles: Role[];
  onClose: () => void;
}) {
  const setRoles = useSetUserRoles();
  const subject = useSubject();
  const [roleIds, setRoleIds] = useState<string[]>([]);
  const [formError, setFormError] = useState<string | null>(null);

  const isSelf = Boolean(user && user.id === subject.id);

  useEffect(() => {
    if (!open) return;
    setRoleIds(user?.roles.map((role) => role.id) ?? []);
    setFormError(null);
  }, [open, user]);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!user) return;
    setRoles.mutate(
      { id: user.id, roleIds },
      {
        onSuccess: () => {
          toast('角色已更新', 'success');
          onClose();
        },
        onError: (error) => setFormError(error.message),
      },
    );
  };

  return (
    <Modal open={open} onClose={onClose} title={`分配角色 · ${user?.username ?? ''}`} width={480}>
      <form className="space-y-3 px-[18px] py-4" onSubmit={handleSubmit}>
        <p className="text-[11.5px] leading-[1.6] text-ink-mute">
          提交后整组替换当前角色，未勾选的会被移除；权限变更在用户下次请求时生效。
        </p>

        {isSelf && (
          <p
            role="status"
            className="flex items-start gap-1.5 rounded-lg border border-[#fbe8c8] bg-[#fff9ee] px-2.5 py-2 text-[11.5px] leading-[1.6] text-[#8a5b00]"
          >
            <TriangleAlert size={12} className="mt-[2px] shrink-0" aria-hidden />
            你正在修改自己的角色。撤销超级管理员后，需要刷新页面才会失去管理入口；
            如果系统里只剩你一个超管，后端会直接拒绝这次修改。
          </p>
        )}

        <RoleCheckboxList
          roles={roles}
          value={roleIds}
          onChange={setRoleIds}
          disabled={setRoles.isPending}
        />

        {formError && (
          <p className="rounded-lg border border-[#fad7d4] bg-[#feeceb] px-2.5 py-2 text-[11.5px] text-[#d94036]" role="alert">
            {formError}
          </p>
        )}

        <div className="flex items-center justify-end gap-1.5 pt-1">
          <Button onClick={onClose} disabled={setRoles.isPending}>
            取消
          </Button>
          <Button variant="primary" type="submit" loading={setRoles.isPending}>
            保存
          </Button>
        </div>
      </form>
    </Modal>
  );
}
