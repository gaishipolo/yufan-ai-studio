import { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { Button, ErrorState, PageHeader } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { useUserDetail } from '@/entities/user/api/user.queries';
import {
  useEffectivePermissions,
  useSetPermissionExceptions,
  useSetUserPermissions,
} from '@/entities/group/api/group.queries';
import { usePermissionCatalog } from '@/entities/role/api/role.queries';
import { PermissionOverview } from '@/features/permission-overview/PermissionOverview';
import { useOverview, type PermissionRow } from '@/features/permission-overview/use-overview';
import { LayerPermissionsModal, type LayerTarget } from '@/features/permission-layers/LayerPermissionsModal';

/**
 * 一个人的权限总览：他有什么、每一条从哪来、以及「只对他关掉」。
 *
 * 这是一条**没有进侧栏**的路由（`module.ts` 里不写 `nav`）：它是「用户与权限」
 * 的下钻页，不是一个新的目的地。好处是能贴链接排障 ——
 * 「张三为什么能删任务」直接发 URL，比让同事照你说的一层层点开强。
 */
export default function UserPermissionsPage() {
  const { userId = '' } = useParams<{ userId: string }>();
  const navigate = useNavigate();

  const user = useUserDetail(userId || null);
  const detail = useEffectivePermissions(userId || null);
  const catalog = usePermissionCatalog();
  const cards = useOverview(detail.data, catalog.data);

  const setExceptions = useSetPermissionExceptions();
  const setPersonal = useSetUserPermissions();
  const [pendingCode, setPendingCode] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);

  const denied = useMemo(() => detail.data?.denied ?? [], [detail.data]);
  const personal = useMemo(() => detail.data?.personal ?? [], [detail.data]);

  // 超管（或持有 `*` 的账号）的权限恒为全集，例外不会生效 —— 写接口也会拒。
  // 界面上整页只读，理由写在页头那一条里（全页唯一的说明文字）。
  const fullPrivilege = (detail.data?.effective ?? []).includes('*');

  const fail = (error: unknown) => toast((error as Error).message, 'error');

  const run = async (code: string, action: () => Promise<unknown>, done: string) => {
    setPendingCode(code);
    try {
      await action();
      toast(done, 'success');
    } catch (error) {
      fail(error);
    } finally {
      setPendingCode(null);
    }
  };

  const deny = (row: PermissionRow) =>
    run(
      row.code,
      () =>
        setExceptions.mutateAsync({ userId, exceptions: [...denied, row.code] }),
      '已对这个账号关闭',
    );

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
      () =>
        setExceptions.mutateAsync({
          userId,
          exceptions: denied.filter((code) => code !== row.code),
        }),
      '已恢复',
    );

  const layerTarget: LayerTarget | null = user.data
    ? { kind: 'user', id: user.data.id, name: user.data.displayName || user.data.username }
    : null;

  const loading = detail.isLoading || catalog.isLoading;

  return (
    <>
      <button
        type="button"
        onClick={() => navigate('/system/user-access')}
        className="mb-2 inline-flex items-center gap-1 text-meta text-ink-soft hover:text-ink"
      >
        <ArrowLeft size={12} /> 用户与权限
      </button>

      <PageHeader
        title={user.data ? user.data.displayName || user.data.username : '权限总览'}
        meta={
          user.data ? (
            <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-mono">{user.data.username}</span>
              {(user.data.roles ?? []).map((role) => (
                <span key={role.id} className="text-micro text-ink-mute">
                  {role.name}
                </span>
              ))}
            </span>
          ) : null
        }
        actions={
          fullPrivilege ? null : (
            <Button variant="secondary" disabled={!user.data} onClick={() => setEditing(true)}>
              编辑个人权限
            </Button>
          )
        }
      />

      {fullPrivilege ? (
        <p className="mt-3 rounded-lg border border-primary-line bg-primary-soft px-3 py-2 text-meta text-ink-soft">
          超级管理员的权限恒为全部，例外对他不生效，所以这里是只读的。
        </p>
      ) : null}

      <div className="mt-3 grid grid-cols-3 gap-2">
        {[
          { label: '生效', value: cards.counts.effective },
          { label: '仅对他关闭', value: cards.counts.denied },
          { label: '被页面关掉', value: cards.counts.gated },
        ].map((item) => (
          <div key={item.label} className="rounded-lg bg-panel-soft px-3 py-2">
            <div className="text-micro text-ink-soft">{item.label}</div>
            <div className="text-body font-semibold tabular-nums text-ink">{item.value}</div>
          </div>
        ))}
      </div>

      <div className="mt-3">
        {detail.isError ? (
          <ErrorState
            error={detail.error}
            title="权限来源加载失败"
            onRetry={() => void detail.refetch()}
          />
        ) : loading ? (
          <div className="py-16 text-center text-xs text-ink-mute">正在读取权限来源…</div>
        ) : (
          <PermissionOverview
            overview={cards}
            pendingCode={pendingCode}
            readOnly={fullPrivilege}
            onDeny={deny}
            onAllow={remove}
            onRestore={restore}
          />
        )}
      </div>

      {layerTarget ? (
        <LayerPermissionsModal
          open={editing}
          target={layerTarget}
          onClose={() => setEditing(false)}
        />
      ) : null}
    </>
  );
}
