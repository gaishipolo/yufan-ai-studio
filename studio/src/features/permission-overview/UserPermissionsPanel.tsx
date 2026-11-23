import { useMemo, useState } from 'react';
import { ErrorState } from '@/shared/ui';
import { Button } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { cn } from '@/shared/lib/cn';
import { useCan } from '@/shared/access/use-access';
import { useUserDetail } from '@/entities/user/api/user.queries';
import type { PermissionTreeUserOut } from '@/shared/api/schema';
import {
  useEffectivePermissions,
  useSetPermissionExceptions,
  useSetUserPermissions,
} from '@/entities/group/api/group.queries';
import { usePermissionCatalog } from '@/entities/role/api/role.queries';
import { LayerPermissionsModal } from '@/features/permission-layers/LayerPermissionsModal';
import { PermissionOverview } from './PermissionOverview';
import { useOverview, type PermissionRow } from './use-overview';

/**
 * 右侧：一个人的权限总览。
 *
 * 从旧的下钻页抽出来的纯展示块 —— 页面外壳（树 / 页签）由 `PermissionsSection`
 * 负责，这里只回答「这个人有什么、从哪来、能不能只关掉这一项」。
 */
export function UserPermissionsPanel({
  userId,
  fallback,
}: {
  userId: string;
  /** 树上那一份简要信息：详情还没回来时先用它渲染标题，避免标题闪一下 */
  fallback?: PermissionTreeUserOut;
}) {
  const user = useUserDetail(userId);
  const detail = useEffectivePermissions(userId);
  const catalog = usePermissionCatalog();
  const cards = useOverview(detail.data, catalog.data);

  const setExceptions = useSetPermissionExceptions();
  const setPersonal = useSetUserPermissions();
  const [pendingCode, setPendingCode] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);

  const canManage = useCan('identity:user:permission:manage');
  const denied = useMemo(() => detail.data?.denied ?? [], [detail.data]);
  const personal = useMemo(() => detail.data?.personal ?? [], [detail.data]);

  // 超管（或持有 `*` 的账号）的权限恒为全集，例外不会生效 —— 写接口也会拒。
  const fullPrivilege = (detail.data?.effective ?? []).includes('*');

  const displayName = user.data?.displayName || user.data?.username || fallback?.displayName || fallback?.username || '';
  const username = user.data?.username || fallback?.username || '';

  const run = async (code: string, action: () => Promise<unknown>, done: string) => {
    setPendingCode(code);
    try {
      await action();
      toast(done, 'success');
    } catch (error) {
      toast((error as Error).message, 'error');
    } finally {
      setPendingCode(null);
    }
  };

  const deny = (row: PermissionRow) =>
    run(row.code, () => setExceptions.mutateAsync({ userId, exceptions: [...denied, row.code] }), '已对这个账号关闭');

  const remove = (row: PermissionRow) =>
    run(
      row.code,
      () =>
        setPersonal.mutateAsync({
          id: userId,
          permissions: personal.filter((code) => code !== row.code),
        }),
      '已移除',
    );

  const restore = (row: PermissionRow) =>
    run(
      row.code,
      () => setExceptions.mutateAsync({ userId, exceptions: denied.filter((code) => code !== row.code) }),
      '已恢复',
    );

  const loading = detail.isLoading || catalog.isLoading;

  return (
    <div className="min-w-0">
      <header className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <h2 className="text-lead font-semibold text-ink">{displayName}</h2>
        <span className="font-mono text-micro text-ink-mute">{username}</span>
        {(user.data?.roles ?? []).map((role) => (
          <span key={role.id} className="text-micro text-ink-mute">
            {role.name}
          </span>
        ))}
        {canManage && !fullPrivilege ? (
          <Button variant="secondary" className="ml-auto" onClick={() => setEditing(true)}>
            编辑个人权限
          </Button>
        ) : null}
      </header>

      {/* 超管没有「清单」可看：任何清单都是假的（他的权限是全集，不是若干条） */}
      {fullPrivilege ? (
        <p className="mt-3 rounded-lg border border-primary-line bg-primary-soft px-3 py-2 text-meta text-ink-soft">
          超级管理员的权限恒为全部，例外对他不生效，所以这里是只读的。
        </p>
      ) : (
        <div className="mt-3 flex flex-wrap gap-2">
          {[
            { label: '生效', value: cards.counts.effective, tone: 'ink' as const },
            { label: '仅对他关闭', value: cards.counts.denied, tone: 'ink' as const },
            {
              label: '被页面关掉',
              value: cards.counts.gated,
              // 有东西被关掉才用警示色：0 的时候染红，等于一直在报警
              tone: cards.counts.gated > 0 ? ('danger' as const) : ('muted' as const),
            },
          ].map((item) => (
            <div key={item.label} className="min-w-[7.5rem] rounded-xl border border-line bg-panel px-3.5 py-2.5">
              <div className="text-micro text-ink-mute">{item.label}</div>
              <div
                className={cn(
                  'mt-0.5 text-title font-semibold leading-none tabular-nums',
                  item.tone === 'danger' && 'text-danger',
                  item.tone === 'muted' && 'text-ink-mute',
                  item.tone === 'ink' && 'text-ink',
                )}
              >
                {item.value}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="mt-3">
        {detail.isError ? (
          <ErrorState
            error={detail.error}
            title="权限来源加载失败"
            onRetry={() => void detail.refetch()}
          />
        ) : loading ? (
          <div className="py-16 text-center text-xs text-ink-mute">正在读取权限来源…</div>
        ) : fullPrivilege ? (
          <p className="rounded-lg border border-line-soft bg-panel-soft px-3 py-6 text-center text-meta text-ink-soft">
            全部权限 · 不需要逐条列出
          </p>
        ) : (
          <PermissionOverview
            overview={cards}
            pendingCode={pendingCode}
            readOnly={!canManage}
            onDeny={deny}
            onAllow={remove}
            onRestore={restore}
          />
        )}
      </div>

      {editing ? (
        <LayerPermissionsModal
          open
          target={{ kind: 'user', id: userId, name: displayName }}
          onClose={() => setEditing(false)}
        />
      ) : null}
    </div>
  );
}
