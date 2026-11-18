import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Loader2 } from 'lucide-react';
import { Button, Chip, Modal } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { toast } from '@/shared/lib/toast';
import { usePermissionCatalog, useSetRolePermissions } from '@/entities/role/api/role.queries';
import type { Role } from '@/entities/role/model/types';
import { PermissionSummaryBar } from '@/features/permission-layers/PermissionSummaryBar';

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
    // 等目录就绪再初始化，否则「有效码」集合是空的，会把所有权限都当成失效
    if (!catalogCodes.size) return;
    setSelected(role.permissions.filter((code) => catalogCodes.has(code)));
    setFormError(null);
  }, [open, role, catalogCodes]);

  const toggle = (code: string) => {
    setSelected((prev) => (prev.includes(code) ? prev.filter((item) => item !== code) : [...prev, code]));
  };

  const toggleGroup = (codes: string[], on: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const code of codes) {
        if (on) next.add(code);
        else next.delete(code);
      }
      return [...next];
    });
  };

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
      width={600}
    >
      <div className="space-y-3 px-[18px] py-4">
        {isSuperuser ? (
          <p className="rounded-lg border border-[#fbe8c8] bg-[#fff6e6] px-2.5 py-2 text-meta leading-[1.6] text-[#b97a10]">
            该角色是超级管理员，权限恒为全部，不提供编辑。
          </p>
        ) : (
          <PermissionSummaryBar
            count={selected.length}
            unit="个权限点"
            onClear={() => setSelected([])}
          />
        )}

        {orphaned.length > 0 && (
          <div className="rounded-lg border border-[#fbe8c8] bg-[#fff6e6] px-2.5 py-2">
            <p className="flex items-center gap-1.5 text-meta font-medium text-[#b97a10]">
              <AlertTriangle size={12} /> 含 {orphaned.length} 个已失效的权限码
            </p>
            <p className="mt-1 font-mono text-meta leading-[1.6] text-[#b97a10]">
              {orphaned.join('、')}
            </p>
            <p className="mt-1 text-meta leading-[1.6] text-[#b97a10]">
              权限点已下线（模块删除或改名），保存时会自动清除，避免「看起来没有却还能访问」。
            </p>
          </div>
        )}

        {isSuperuser ? null : catalogQuery.isLoading ? (
          <div className="flex items-center justify-center gap-2 py-10 text-xs text-ink-mute">
            <Loader2 size={14} className="animate-spin" /> 正在加载权限点…
          </div>
        ) : catalogQuery.isError ? (
          <p className="py-8 text-center text-xs text-danger">
            权限点加载失败：{catalogQuery.error.message}
          </p>
        ) : (
          <div className="max-h-[46vh] space-y-3 overflow-auto pr-1">
            {(catalogQuery.data?.groups ?? []).map((group) => {
              const codes = group.items.map((item) => item.code);
              const allChecked = codes.every((code) => selected.includes(code));
              return (
                <section key={group.group} className="rounded-lg border border-line-soft">
                  <header className="flex items-center gap-2 border-b border-line-soft bg-panel-soft px-2.5 py-1.5">
                    <h3 className="text-xs font-semibold text-ink-soft">{group.group}</h3>
                    <span className="text-meta text-ink-mute tabular-nums">{codes.length}</span>
                    <button
                      type="button"
                      className="ml-auto text-meta text-primary hover:underline"
                      onClick={() => toggleGroup(codes, !allChecked)}
                    >
                      {allChecked ? '取消本组' : '全选本组'}
                    </button>
                  </header>
                  <ul className="grid gap-1 p-1.5 md:grid-cols-2">
                    {group.items.map((item) => {
                      const checked = selected.includes(item.code);
                      return (
                        <li key={item.code}>
                          <label
                            className={cn(
                              'flex cursor-pointer items-start gap-2 rounded-[7px] px-2 py-1.5 transition-colors',
                              checked ? 'bg-primary-soft' : 'hover:bg-panel-soft',
                            )}
                          >
                            <input
                              type="checkbox"
                              className="mt-[3px] h-3 w-3 shrink-0 accent-primary"
                              checked={checked}
                              onChange={() => toggle(item.code)}
                            />
                            <span className="min-w-0">
                              <span className="flex items-center gap-1.5">
                                <span className="text-xs text-ink">{item.name}</span>
                                <Chip>{item.code}</Chip>
                              </span>
                              {item.description && (
                                <span className="mt-0.5 block text-meta leading-[1.5] text-ink-mute">
                                  {item.description}
                                </span>
                              )}
                            </span>
                          </label>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              );
            })}
          </div>
        )}

        {formError && (
          <p className="rounded-lg border border-[#fad7d4] bg-[#feeceb] px-2.5 py-2 text-meta text-[#d94036]" role="alert">
            {formError}
          </p>
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
