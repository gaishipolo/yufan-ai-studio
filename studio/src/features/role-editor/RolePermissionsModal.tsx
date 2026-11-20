import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Button, Modal, Notice } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { usePermissionCatalog, useSetRolePermissions } from '@/entities/role/api/role.queries';
import { isResolvablePermission } from '@/entities/role/model/permission-catalog';
import type { Role } from '@/entities/role/model/types';
import { PermissionPicker } from '@/features/permission-layers/PermissionPicker';

/**
 * 勾选角色权限（`PUT /roles/{id}/permissions`，整组替换）。
 *
 * 两个必须处理的边界：
 * - 失效权限（`orphanedPermissions`）：库里残留但目录里已不存在的码，后端会拒绝提交，
 *   所以初始化时把它排除，并在顶部提示「保存后自动清除」。
 * - 超管角色：权限恒为 `*`，勾选没有意义，直接说明并禁用保存。
 */
export function RolePermissionsModal({
  open,
  role,
  onClose,
}: {
  open: boolean;
  role: Role | null;
  onClose: () => void;
}) {
  const catalogQuery = usePermissionCatalog();
  const setPermissions = useSetRolePermissions();

  const [selected, setSelected] = useState<string[]>([]);
  const [formError, setFormError] = useState<string | null>(null);

  const catalogCodes = useMemo(() => {
    const codes = new Set<string>();
    for (const group of catalogQuery.data?.groups ?? []) {
      for (const item of group.items) codes.add(item.code);
    }
    return codes;
  }, [catalogQuery.data]);

  useEffect(() => {
    if (!open || !role) return;
    // 等目录就绪再初始化：选择器要靠它渲染两层结构
    if (!catalogCodes.size) return;
    /* 判据是「后端认不认」，不是「目录里渲染不渲染」——这两件事不一样：
       `task:*` / `image:*` 不在目录里（目录只有具体码），但后端明确接受它们。
       按目录过滤会把它悄悄从选中集合里删掉，于是**打开弹窗点保存就等于撤权**
       （内置「内容编辑」就靠这两条通配覆盖十几个接口）。 */
    setSelected(role.permissions.filter(isResolvablePermission));
    setFormError(null);
  }, [open, role, catalogCodes]);

  const isSuperuser = role?.isSuperuser ?? false;
  const orphaned = role?.orphanedPermissions ?? [];

  const handleSave = () => {
    if (!role) return;
    setFormError(null);
    setPermissions.mutate(
      { id: role.id, permissions: selected },
      {
        onSuccess: () => {
          toast('角色权限已更新', 'success');
          onClose();
        },
        onError: (error) => setFormError(error.message),
      },
    );
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={isSuperuser ? `角色权限 · ${role?.name ?? ''}` : `编辑角色权限 · ${role?.name ?? ''}`}
      width={1000}
    >
      <div className="space-y-3 px-[18px] py-4">
        {isSuperuser ? (
          <Notice tone="warning">该角色是超级管理员，权限恒为全部，不提供编辑。</Notice>
        ) : null}

        {orphaned.length > 0 && (
          <Notice
            tone="warning"
            title={
              <span className="flex items-center gap-1.5">
                <AlertTriangle size={12} /> 含 {orphaned.length} 个已失效的权限码
              </span>
            }
          >
            <span className="block font-mono">{orphaned.join('、')}</span>
            <span className="mt-1 block">
              权限点已下线（模块删除或改名），保存时会自动清除，避免「看起来没有却还能访问」。
            </span>
          </Notice>
        )}

        {isSuperuser ? null : (
          /* 勾选清单由 PermissionPicker 提供：它把「页面 → 接口」的层级画在一屏里，
             而这里原来自己抄了一份平铺清单 —— 两份列表迟早漂移（重复的 bug 修完一次
             就够，两份都要修）。 */
          <PermissionPicker selected={selected} onChange={setSelected} />
        )}

        {formError && (
          <Notice tone="error">{formError}</Notice>
        )}

        <div className="flex items-center justify-end gap-1.5 pt-1">
          <Button onClick={onClose} disabled={setPermissions.isPending}>
            {isSuperuser ? '关闭' : '取消'}
          </Button>
          {!isSuperuser && (
            <Button
              variant="primary"
              onClick={handleSave}
              loading={setPermissions.isPending}
              disabled={catalogQuery.isLoading}
            >
              保存
            </Button>
          )}
        </div>
      </div>
    </Modal>
  );
}
