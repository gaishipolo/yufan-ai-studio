import { useState } from 'react';
import { AlertCircle, AlertTriangle, Plus, RefreshCw, ShieldAlert, Users } from 'lucide-react';
import { Button, Chip, EmptyState, Skeleton } from '@/shared/ui';
import { Can } from '@/shared/access/Can';
import { useCan } from '@/shared/access/use-access';
import { toast } from '@/shared/lib/toast';
import {
  useDeleteRole,
  useRoleList,
} from '@/entities/role/api/role.queries';
import {
  cannotDeleteReason,
  permissionCountLabel,
  type Role,
} from '@/entities/role/model/types';
import { RoleFormModal } from '@/features/role-editor/RoleFormModal';
import { RolePermissionsModal } from '@/features/role-editor/RolePermissionsModal';

type Dialog = 'create' | 'edit' | 'permissions' | null;

function RoleCard({ role, onOpen }: { role: Role; onOpen: (dialog: Dialog, role: Role) => void }) {
  const canManage = useCan('identity:role:manage');
  const deleteRole = useDeleteRole();
  const [confirming, setConfirming] = useState(false);

  const blockedReason = cannotDeleteReason(role);

  return (
    <section className="flex h-full flex-col rounded-card border border-line-soft bg-panel p-3.5">
      <header className="flex flex-wrap items-center gap-1.5">
        <h3 className="text-[13px] font-semibold text-ink">{role.name}</h3>
        <Chip>{role.code}</Chip>
        {role.isSuperuser && <Chip tone="warn">超管</Chip>}
        {role.isBuiltin && !role.isSuperuser && <Chip>内置</Chip>}
      </header>

      <p className="mt-1 min-h-[32px] text-[11.5px] leading-[1.6] text-ink-mute">
        {role.description || '暂无说明'}
      </p>

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-soft">
        <span className="inline-flex items-center gap-1">
          <Users size={11} /> {role.userCount} 名成员
        </span>
        <span className="inline-flex items-center gap-1">
          <ShieldAlert size={11} /> {permissionCountLabel(role)}
        </span>
      </div>

      {role.orphanedPermissions.length > 0 && (
        <p className="mt-2 flex items-start gap-1.5 rounded-lg bg-[#fff6e6] px-2 py-1.5 text-[11px] leading-[1.5] text-[#b97a10]">
          <AlertTriangle size={11} className="mt-[2px] shrink-0" />
          有 {role.orphanedPermissions.length} 个权限码已下线，编辑权限时会自动清除
        </p>
      )}

      {/* mt-auto：卡片被同行拉高时，操作区始终贴底，视觉上四张卡对齐 */}
      <div className="mt-auto flex flex-wrap items-center gap-1.5 border-t border-line-soft pt-2.5">
        <Button
          size="sm"
          disabled={!canManage || role.isSuperuser}
          title={role.isSuperuser ? '超管权限恒为全部' : undefined}
          onClick={() => onOpen('permissions', role)}
        >
          编辑权限
        </Button>
        <Button size="sm" variant="ghost" disabled={!canManage} onClick={() => onOpen('edit', role)}>
          编辑
        </Button>

        <span className="ml-auto" />
        {confirming ? (
          <>
            <span className="text-[11px] text-ink-soft">确认删除？</span>
            <Button
              size="sm"
              variant="danger"
              loading={deleteRole.isPending}
              onClick={() =>
                deleteRole.mutate(role.id, {
                  onSuccess: () => {
                    toast('角色已删除', 'success');
                    setConfirming(false);
                  },
                  onError: (error) => {
                    toast(error.message, 'error');
                    setConfirming(false);
                  },
                })
              }
            >
              删除
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
              取消
            </Button>
          </>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            className="text-danger hover:bg-[#feeceb] hover:text-danger"
            disabled={!canManage || Boolean(blockedReason)}
            title={blockedReason ?? undefined}
            onClick={() => setConfirming(true)}
          >
            删除
          </Button>
        )}
      </div>
    </section>
  );
}

export function RolesSection() {
  const rolesQuery = useRoleList();
  const [dialog, setDialog] = useState<Dialog>(null);
  const [active, setActive] = useState<Role | null>(null);

  const open = (next: Dialog, role: Role) => {
    setActive(role);
    setDialog(next);
  };
  const close = () => setDialog(null);

  const roles = rolesQuery.data ?? [];

  return (
    <>
      <div className="flex flex-wrap items-center gap-1.5">
        <p className="text-[11.5px] leading-[1.6] text-ink-mute">
          角色是权限点的集合。给用户分配角色即授予对应权限；内置角色不可删除。
        </p>
        <span className="ml-auto text-[11.5px] text-ink-mute">共 {roles.length} 个角色</span>
        <Can permission="identity:role:manage">
          <Button
            size="sm"
            variant="primary"
            icon={<Plus size={13} />}
            onClick={() => {
              setActive(null);
              setDialog('create');
            }}
          >
            新建角色
          </Button>
        </Can>
      </div>

      <div className="mt-3">
        {rolesQuery.isLoading ? (
          <div className="grid gap-3 md:grid-cols-2">
            {Array.from({ length: 3 }).map((_, index) => (
              <Skeleton key={index} className="h-[148px] w-full" />
            ))}
          </div>
        ) : rolesQuery.isError ? (
          <div className="flex flex-col items-center gap-2 rounded-card border border-line-soft bg-panel px-4 py-10 text-center">
            <AlertCircle size={22} className="text-danger" />
            <p className="text-xs text-ink-soft">角色列表加载失败：{rolesQuery.error.message}</p>
            <Button size="sm" icon={<RefreshCw size={12} />} onClick={() => void rolesQuery.refetch()}>
              重试
            </Button>
          </div>
        ) : roles.length === 0 ? (
          <div className="rounded-card border border-line-soft bg-panel">
            <EmptyState
              icon={<Users size={36} strokeWidth={1.4} />}
              title="还没有角色"
              description="先建一个角色并勾选权限，再到「用户」页签分配"
            />
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {roles.map((role) => (
              <RoleCard key={role.id} role={role} onOpen={open} />
            ))}
          </div>
        )}
      </div>

      <RoleFormModal open={dialog === 'create' || dialog === 'edit'} role={dialog === 'edit' ? active : null} onClose={close} />
      <RolePermissionsModal open={dialog === 'permissions'} role={active} onClose={close} />
    </>
  );
}
